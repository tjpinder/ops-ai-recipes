/*
  Dead SSRS report finder
  ops-ai-recipes, recipe 01. MIT license.

  Run against the ReportServer database with a read-only login
  (db_datareader is enough). Reads catalog metadata, report definitions,
  subscriptions and the execution log. Never reads data source credentials,
  report parameters, subscription recipients, or business data.

  Sorts every report into one status:
    retire candidate        no runs in the window, no active subscription,
                            not a subreport, not a linked report, older than the window
    review                  no runs in the window, but an active subscription is sending it
    not logged (subreport)  SSRS logs the parent, not the subreport, so no runs proves nothing
    not logged (linked)     SSRS does not log linked reports directly
    too new to judge        created inside the window
    active                  ran at least once in the window

  It refuses to judge (and stops) when the execution log does not cover the
  whole window you asked about.
*/

SET NOCOUNT ON;
SET QUOTED_IDENTIFIER ON;  -- the XML methods below need it; sqlcmd turns it off by default

DECLARE @AsOf       datetime = GETDATE();  -- end of the window; tests pin this
DECLARE @WindowDays int      = NULL;       -- NULL = use ExecutionLogDaysKept

DECLARE @DaysKept int =
    (SELECT TRY_CAST(CAST(Value AS nvarchar(50)) AS int)
     FROM dbo.ConfigurationInfo WHERE Name = 'ExecutionLogDaysKept');

SET @WindowDays = COALESCE(@WindowDays, @DaysKept, 60);

DECLARE @WindowStart datetime = DATEADD(DAY, -@WindowDays, @AsOf);
DECLARE @OldestLog   datetime = (SELECT MIN(TimeStart) FROM dbo.ExecutionLog3);

-- Guardrail: a report can only be "unused" over a window the log actually covers.
IF @OldestLog IS NULL OR @OldestLog > @WindowStart
BEGIN
    DECLARE @msg nvarchar(400) = CONCAT(
        'Refusing to judge: the execution log starts at ',
        COALESCE(CONVERT(nvarchar(19), @OldestLog, 126), 'nothing (empty log)'),
        ' but the window starts at ', CONVERT(nvarchar(19), @WindowStart, 126),
        '. ExecutionLogDaysKept = ', COALESCE(CAST(@DaysKept AS nvarchar(10)), 'unknown'),
        '. Use a shorter window or wait for the log to fill.');
    RAISERROR(@msg, 16, 1);
    RETURN;
END;

WITH reports AS (
    SELECT ItemID, Path, Name, Type, Hidden, CreationDate, ModificationDate, Content,
           LEFT(Path, LEN(Path) - CHARINDEX('/', REVERSE(Path))) AS Folder
    FROM dbo.Catalog
    WHERE Type IN (2, 4)               -- 2 = report, 4 = linked report
),
subreport_refs AS (                   -- every subreport any report definition points at
    SELECT DISTINCT
        CASE WHEN ref.ReportName LIKE '/%' THEN ref.ReportName
             ELSE r.Folder + '/' + ref.ReportName END AS TargetPath
    FROM reports r
    CROSS APPLY (SELECT TRY_CONVERT(xml, CONVERT(varbinary(max), r.Content)) AS Rdl) x
    CROSS APPLY x.Rdl.nodes('//*:Subreport/*:ReportName') AS s(n)
    CROSS APPLY (SELECT s.n.value('.', 'nvarchar(425)') AS ReportName) ref
    WHERE r.Type = 2
),
runs AS (
    SELECT ItemPath,
           COUNT(*) AS RunsInWindow,
           SUM(CASE WHEN RequestType = 'Interactive'  THEN 1 ELSE 0 END) AS InteractiveRuns,
           SUM(CASE WHEN RequestType = 'Subscription' THEN 1 ELSE 0 END) AS SubscriptionRuns,
           COUNT(DISTINCT UserName) AS DistinctUsers,
           MAX(TimeStart) AS LastRun
    FROM dbo.ExecutionLog3
    WHERE TimeStart >= @WindowStart AND TimeStart <= @AsOf
    GROUP BY ItemPath
),
subs AS (
    SELECT Report_OID,
           SUM(CASE WHEN InactiveFlags = 0 THEN 1 ELSE 0 END) AS ActiveSubscriptions,
           MAX(LastRunTime) AS LastSubscriptionRun
    FROM dbo.Subscriptions
    GROUP BY Report_OID
)
SELECT
    CASE
        WHEN ru.RunsInWindow > 0                       THEN 'active'
        WHEN r.Type = 4                                THEN 'not logged (linked)'
        WHEN sr.TargetPath IS NOT NULL                 THEN 'not logged (subreport)'
        WHEN r.CreationDate > @WindowStart             THEN 'too new to judge'
        WHEN COALESCE(su.ActiveSubscriptions, 0) > 0   THEN 'review'
        ELSE 'retire candidate'
    END                                   AS Status,
    r.Path,
    CASE r.Type WHEN 2 THEN 'report' ELSE 'linked report' END AS ItemType,
    r.Hidden,
    CONVERT(date, r.CreationDate)         AS Created,
    CONVERT(date, r.ModificationDate)     AS LastModified,
    COALESCE(ru.RunsInWindow, 0)          AS RunsInWindow,
    COALESCE(ru.InteractiveRuns, 0)       AS InteractiveRuns,
    COALESCE(ru.SubscriptionRuns, 0)      AS SubscriptionRuns,
    COALESCE(ru.DistinctUsers, 0)         AS DistinctUsers,
    ru.LastRun,
    COALESCE(su.ActiveSubscriptions, 0)   AS ActiveSubscriptions,
    su.LastSubscriptionRun,
    @WindowDays                           AS WindowDays,
    CONVERT(date, @WindowStart)           AS WindowStart,
    CONVERT(date, @AsOf)                  AS WindowEnd
FROM reports r
LEFT JOIN runs ru           ON ru.ItemPath = r.Path
LEFT JOIN subreport_refs sr ON sr.TargetPath = r.Path
LEFT JOIN subs su           ON su.Report_OID = r.ItemID
ORDER BY
    CASE
        WHEN ru.RunsInWindow > 0 THEN 9
        WHEN r.Type = 4 OR sr.TargetPath IS NOT NULL THEN 3
        WHEN r.CreationDate > @WindowStart THEN 4
        WHEN COALESCE(su.ActiveSubscriptions, 0) > 0 THEN 2
        ELSE 1
    END,
    r.Path;
