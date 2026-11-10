-- =========================================================================
-- Network health KPI queries (SQLite)
--
-- These are the documented versions of the queries executed by
-- src/gateway_telemetry/repository.py. :since is a UTC ISO timestamp in the
-- canonical form YYYY-MM-DDTHH:MM:SSZ.
-- =========================================================================

-- 1. Fleet overview -------------------------------------------------------
-- Current state uses the latest sample per device; rates use the whole window.
WITH windowed AS (
    SELECT * FROM telemetry WHERE ts >= :since
),
latest AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY device_id ORDER BY ts DESC) AS rn
    FROM windowed
),
current AS (
    SELECT * FROM latest WHERE rn = 1
),
stats AS (
    SELECT
        (SELECT COUNT(*) FROM devices)                                   AS total_devices,
        (SELECT COUNT(*) FROM current WHERE wan_status = 'online')       AS online_devices,
        COALESCE((SELECT AVG(latency_ms)        FROM windowed), 0)       AS avg_latency_ms,
        COALESCE((SELECT AVG(packet_loss_pct)   FROM windowed), 0)       AS avg_packet_loss_pct,
        COALESCE((SELECT AVG(api_latency_ms)    FROM windowed), 0)       AS avg_api_latency_ms,
        COALESCE((SELECT AVG(cpu_pct)           FROM windowed), 0)       AS avg_cpu_pct,
        COALESCE((SELECT AVG(memory_pct)        FROM windowed), 0)       AS avg_memory_pct,
        COALESCE((
            SELECT 100.0 * SUM(CASE WHEN http_status >= 500 THEN 1 ELSE 0 END) / COUNT(*)
            FROM windowed
        ), 0)                                                            AS api_error_rate_pct,
        (SELECT COUNT(*) FROM windowed)                                  AS sample_count,
        (SELECT COUNT(*) FROM diagnoses WHERE created_at >= :since)      AS incident_count
)
SELECT
    *,
    CASE WHEN sample_count = 0 THEN 0.0 ELSE ROUND(MAX(0.0, MIN(100.0,
        100.0
        - 40.0 * (1.0 - 1.0 * online_devices / NULLIF(total_devices, 0))
        - MIN(25.0, avg_packet_loss_pct * 5.0)
        - MIN(20.0, avg_latency_ms / 20.0)
        - MIN(15.0, api_error_rate_pct)
    )), 1) END AS network_health_score
FROM stats;

-- 2. Time series (1-minute buckets) --------------------------------------
SELECT
    substr(ts, 1, 16) || ':00Z' AS bucket,
    ROUND(AVG(latency_ms), 2)       AS avg_latency_ms,
    ROUND(AVG(packet_loss_pct), 3)  AS avg_packet_loss_pct,
    ROUND(AVG(api_latency_ms), 2)   AS avg_api_latency_ms,
    ROUND(AVG(cpu_pct), 1)          AS avg_cpu_pct,
    ROUND(AVG(memory_pct), 1)       AS avg_memory_pct,
    ROUND(
        100.0 * SUM(CASE WHEN http_status >= 500 THEN 1 ELSE 0 END) / COUNT(*),
        2
    ) AS api_error_rate_pct
FROM telemetry
WHERE ts >= :since
GROUP BY bucket
ORDER BY bucket;

-- 3. Root-cause distribution ---------------------------------------------
SELECT
    root_cause,
    category,
    COUNT(*)                  AS incidents,
    ROUND(AVG(confidence), 3) AS avg_confidence,
    MAX(created_at)           AS last_seen
FROM diagnoses
WHERE created_at >= :since
GROUP BY root_cause, category
ORDER BY incidents DESC;

-- 4. Per-device signal snapshot for the classifier -----------------------
SELECT
    COUNT(*)                                                              AS samples,
    COALESCE(AVG(CASE WHEN wan_status = 'online' THEN 1.0 ELSE 0.0 END), 0) AS wan_online_ratio,
    COALESCE(AVG(packet_loss_pct), 0)                                     AS avg_packet_loss_pct,
    COALESCE(AVG(cpu_pct), 0)                                             AS avg_cpu_pct,
    COALESCE(AVG(memory_pct), 0)                                          AS avg_memory_pct,
    COALESCE(AVG(api_latency_ms), 0)                                      AS avg_api_latency_ms,
    COALESCE(AVG(CASE WHEN http_status >= 500 THEN 1.0 ELSE 0.0 END), 0)  AS api_error_ratio,
    COALESCE(AVG(dns_lookup_ms), 0)                                       AS avg_dns_lookup_ms,
    COALESCE(AVG(wifi_rssi_avg), 0)                                       AS avg_wifi_rssi,
    COALESCE(AVG(wifi_clients), 0)                                        AS avg_wifi_clients
FROM telemetry
WHERE device_id = :device_id AND ts >= :since;

-- 5. Top failing components ----------------------------------------------
SELECT component, COUNT(*) AS events
FROM device_logs
WHERE ts >= :since AND severity IN ('ERROR', 'CRITICAL')
GROUP BY component
ORDER BY events DESC
LIMIT 10;
