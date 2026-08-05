DELETE FROM monitoring_events
WHERE event_id NOT IN (
    SELECT max(event_id) FROM monitoring_events GROUP BY batch_name, report_type
);
SELECT batch_name, count(*) AS n FROM monitoring_events
WHERE report_type = 'data_drift' GROUP BY batch_name ORDER BY batch_name;
