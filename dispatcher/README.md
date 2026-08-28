# FlyCam Dispatcher

Local or server-side dispatcher for FlyCam Drone Control Center. It stores live
vehicle telemetry, a retained telemetry history, delivery missions and operator
events in SQLite. The browser dashboard is available at `http://127.0.0.1:8088/`.

The service deliberately has no endpoint for flight control or cargo-bay actuation.

## Installed Windows build

The FlyCam Windows installer includes `FlyCam-Dispatcher-Server.exe`. When the
server address in FlyCam is `http://127.0.0.1:8088`, the application starts and
stops this local server automatically. Enable telemetry in **Cargo Bay → Server**
and use **Open in browser** to view the dashboard.

The SQLite database and server log are stored in the current user's local
application-data directory, so the installation folder remains read-only.

## Run directly

Requires Python 3.11 or newer and no third-party packages.

```bash
python server.py --host 127.0.0.1 --port 8088
```

For a network-accessible deployment, set a strong API key and place the service
behind HTTPS or a VPN:

```bash
FLYCAM_API_KEY="replace-with-a-long-random-key" \
python server.py --host 0.0.0.0 --port 8088
```

Enter the same URL and API key in **Cargo Bay → Server** inside the custom QGC.

On Windows, the helper script creates the data directory and starts the local
service from source when the packaged executable is not available:

```powershell
.\dispatcher\run_windows.ps1
```

Then open <http://127.0.0.1:8088/>. To listen on a network interface, pass
`-ListenAddress 0.0.0.0 -ApiKey "a-long-random-key"` and use a firewall plus
HTTPS or VPN.

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

When `FLYCAM_API_KEY` is set, pass it in `X-API-Key` or as a Bearer token.

## Test

```bash
python -m unittest -v dispatcher.test_server
```
