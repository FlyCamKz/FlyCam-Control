# FlyCam Dispatcher

Local or server-side dispatcher for FlyCam AeroScope/AgroScope Drone Control Center. It stores live
vehicle telemetry, a retained telemetry history, delivery missions, operator
events, video-analytics detections and a security audit in SQLite. The browser
dashboard is available at `http://127.0.0.1:8088/`.

The service deliberately has no endpoint for flight control or cargo-bay actuation.

## Installed Windows build

The FlyCam Windows installer includes `FlyCam-Dispatcher-Server.exe`. When the
server address in FlyCam is `http://127.0.0.1:8088`, the application starts and
stops this local server automatically. Enable telemetry in **Cargo Bay → Server**
and use **Open in browser** to view the dashboard.

The SQLite database and server log are stored in the current user's local
application-data directory, so the installation folder remains read-only.

## Run directly

Requires Python 3.11 or newer. Install the pinned encryption dependency first:

```bash
python -m pip install -r dispatcher/requirements.txt
python server.py --host 127.0.0.1 --port 8088
```

For a network-accessible deployment, use TLS and separate strong keys for each
role. The server refuses an unauthenticated non-loopback listener by default:

```bash
FLYCAM_TLS_CERT="/etc/flycam/server.pem" \
FLYCAM_TLS_KEY="/etc/flycam/server.key" \
FLYCAM_ADMIN_KEY="a-long-random-admin-key" \
FLYCAM_INGEST_KEY="a-different-long-random-key" \
FLYCAM_VIEWER_KEY="another-long-random-key" \
FLYCAM_OPERATOR_KEY="third-long-random-key" \
python server.py --host 0.0.0.0 --port 8443
```

Enter the HTTPS URL and ingest key in **Cargo Bay → Server** inside the custom
QGC. An internal CA must be installed in the trusted Windows certificate store.
The legacy `FLYCAM_API_KEY` remains available and grants the `admin` role;
new deployments should use the explicit `FLYCAM_ADMIN_KEY`.

Roles are intentionally separated:

- `ingest`: POST telemetry, events and detections;
- `viewer`: read telemetry, missions, events and detections;
- `operator`: viewer rights plus create/update missions;
- `admin`: every operation plus security-audit access.

Optional mTLS is enabled with `FLYCAM_TLS_CA=/path/ca.pem` and
`FLYCAM_REQUIRE_CLIENT_CERT=1`.

## Encryption of data at rest

Sensitive JSON payloads and mission text fields can be protected before they
reach SQLite with versioned AES-256-GCM envelopes. Authentication tags detect
modification, and the field context is authenticated to prevent substitution
between tables. Generate a 256-bit key locally:

```bash
python -m dispatcher.crypto --generate-key
```

Configure the returned value through the process environment, never in Git:

```bash
FLYCAM_DATA_KEYS="2026-09:<generated-base64-key>" \
FLYCAM_ACTIVE_DATA_KEY="2026-09" \
FLYCAM_REQUIRE_DATA_ENCRYPTION=1 \
python dispatcher/server.py --host 127.0.0.1 --port 8088
```

For rotation, add the new key alongside the old key and change only the active
identifier. Old keys must remain available while records encrypted with them
exist. Existing plaintext rows remain readable for controlled migration; every
new or updated protected value uses the active key. `/health` reports whether
encryption is active, the provider identifier and active key ID, but never key
material.

This AES-GCM provider is an engineering security control, not a claim of
Kazakhstan national cryptographic certification. A provider approved for the
selected certification scheme must be integrated and evaluated separately.

On Windows, the helper script creates the data directory and starts the local
service from source when the packaged executable is not available:

```powershell
.\dispatcher\run_windows.ps1
```

Then open <http://127.0.0.1:8088/>. The PowerShell helper also accepts role keys,
certificate paths and `-RequireClientCertificate`; see its parameter list. Keep
the service behind a firewall even when TLS is enabled.

## Docker

```bash
cp .env.example .env
docker compose up --build -d
```

The supplied Compose file exposes the service on localhost only. Change the port
binding only when firewall, authentication and TLS/VPN protection are in place.

## API

- `GET /health`
- `POST /api/v1/telemetry`
- `GET /api/v1/vehicles`
- `GET /api/v1/telemetry?vehicleId=1&limit=300`
- `POST|GET /api/v1/missions`
- `PATCH /api/v1/missions/{id}`
- `POST|GET /api/v1/events`
- `POST|GET /api/v1/detections`
- `GET /api/v1/security/audit` (`admin` only)

When `FLYCAM_API_KEY` is set, pass it in `X-API-Key` or as a Bearer token.
Role-specific keys use the same header. Detection POST supports one object or a
batch `{ "detections": [...] }` with up to 100 objects.

The bundled video sidecar is documented in [`../analytics/README_RU.md`](../analytics/README_RU.md).
Deployment and certification boundaries are described in
[`../docs/certification/SECURITY_PROFILE_RU.md`](../docs/certification/SECURITY_PROFILE_RU.md).

## Test

```bash
python -m unittest -v dispatcher.test_crypto dispatcher.test_server analytics.test_video_analytics
```
