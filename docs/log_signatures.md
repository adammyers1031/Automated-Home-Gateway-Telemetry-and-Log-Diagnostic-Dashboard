# Log signature catalogue

Each signature is matched against the message body, most specific first. The
first match wins. Weights feed the diagnostic score; severity multiplies them.

| Code | Category | Root cause | Pattern intent | Weight | Recommended action |
|---|---|---|---|---|---|
| `PPPOE_AUTH_FAILED` | wan | PPPoE authentication failure | PPPoE/CHAP reject | 4.5 | Verify ISP credentials; escalate to ISP |
| `DHCP_TIMEOUT` | wan | DHCP lease acquisition failure | discover timeout / no offer | 3.5 | Release/renew lease; check ISP handoff |
| `WAN_LINK_DOWN` | wan | WAN uplink loss | carrier/link down | 5.0 | Check cabling/ONT and ISP status |
| `DNS_RESOLVE_FAILED` | dns | DNS resolution failure | SERVFAIL, NXDOMAIN, resolver timeout | 3.0 | Validate DNS servers; clear cache |
| `TLS_HANDSHAKE_FAILED` | security | TLS handshake failure | handshake/cert failure | 3.0 | Check trust store and certificate expiry |
| `API_TIMEOUT` | backend | Backend API degradation | request timed out | 3.0 | Inspect error budgets; retry with backoff |
| `API_5XX` | backend | Backend API degradation | HTTP 5xx / upstream error | 3.0 | Inspect error budgets; retry with backoff |
| `WIFI_DEAUTH_FLOOD` | wifi | Wi-Fi client instability | deauth/disassoc flood | 2.0 | Review channel use and RSSI |
| `CONNTRACK_FULL` | resource | Resource exhaustion | conntrack table full | 3.5 | Tune limits; check for traffic storm |
| `MEMORY_PRESSURE` | resource | Resource exhaustion | OOM / memory pressure | 4.0 | Profile memory; restart leaking service |
| `FIRMWARE_CRASH` | stability | Firmware crash/reboot | kernel panic / watchdog | 5.0 | Collect dumps; roll back firmware |
| `NTP_SYNC_FAILED` | time | NTP synchronization failure | NTP unreachable | 1.5 | Check UDP/123 egress |

## Telemetry-derived evidence

| Evidence code | Trigger | Root cause |
|---|---|---|
| `TELEMETRY_WAN_OFFLINE` | WAN online ratio < 50 % | WAN uplink loss |
| `TELEMETRY_PACKET_LOSS` | average loss ≥ 8 % | WAN uplink loss |
| `TELEMETRY_MEMORY_PRESSURE` | average memory ≥ 90 % | Resource exhaustion |
| `TELEMETRY_CPU_PRESSURE` | average CPU ≥ 90 % | Resource exhaustion |
| `TELEMETRY_API_LATENCY` | average API latency ≥ 1000 ms | Backend API degradation |
| `TELEMETRY_API_ERRORS` | 5xx ratio ≥ 10 % | Backend API degradation |
| `TELEMETRY_DNS_LATENCY` | average DNS lookup ≥ 500 ms | DNS resolution failure |
| `TELEMETRY_WIFI_QUALITY` | RSSI ≤ −70 dBm **and** ≥ 25 clients | Wi-Fi client instability |

## Severity mapping

| Log severity | Multiplier |
|---|---|
| DEBUG | 0.1 |
| INFO | 0.2 |
| WARN | 1.0 |
| ERROR | 2.0 |
| CRITICAL | 3.0 |

Diagnosis severity:

- **critical** — any CRITICAL log or weighted score ≥ 10;
- **warning** — any ERROR log or weighted score ≥ 4;
- **info** — weaker evidence;
- **healthy** — no evidence at all.
