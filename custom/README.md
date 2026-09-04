# FlyCam AeroScope/AgroScope Drone Control Center

Custom FlyCam build based on QGroundControl v5.1.3.

Product name: **FlyCam AeroScope/AgroScope Drone Control Center**

Brand: **FlyCam**

Windows display title: **FlyCam — AeroScope/AgroScope Drone Control Center**

## First-release profile

- Windows x64
- PX4 / Cube Orange
- USB/COM and RFD900/SiK telemetry
- Standard QGroundControl map, mission planning and telemetry
- Multi-vehicle map, active-vehicle selector and confirmed group actions
- FlyCam branding
- Protected Futaba S3001 cargo-bay control through PX4 generic actuators
- Local JSONL cargo command audit log
- Optional telemetry and event uplink for every connected vehicle to the FlyCam dispatcher
- Standalone RTSP/HTTP/USB/file video analytics for people and vehicles (ONNX model supplied separately)
- TLS 1.2+, optional mTLS, role-scoped API keys and security audit for the dispatcher
- Integrated QGC video display remains disabled until the production camera/protocol is selected
- ArduPilot UI disabled

The direct **Open** action is available only after PX4 finishes connecting and
the vehicle is disarmed and not flying. **Close** remains available while the
vehicle is connected. PX4 command acknowledgement is shown separately from
physical position: without a limit switch, neither QGC nor the dispatcher claims
that the door actually moved.

See [HARDWARE_SETUP_RU.md](HARDWARE_SETUP_RU.md) before connecting or testing the
servo. The remaining acceptance work is listed in
[RELEASE_CHECKLIST_RU.md](RELEASE_CHECKLIST_RU.md). The dispatcher service and
its tests are in [`../dispatcher`](../dispatcher). Video analytics is documented
in [`../analytics/README_RU.md`](../analytics/README_RU.md); the security and
certification-preparation set starts at
[`../docs/certification/SECURITY_PROFILE_RU.md`](../docs/certification/SECURITY_PROFILE_RU.md).

## Configure

Windows prerequisites are Qt 6.11.1 for MSVC 2022 x64, CMake, Ninja, Visual
Studio 2022 C++ tools and NSIS. From PowerShell at the repository root:

```powershell
$env:QT_ROOT_DIR = "C:\Qt\6.11.1\msvc2022_64"
.\custom\tools\build_windows.ps1
```

The GitHub Actions workflow `.github/workflows/flycam-windows.yml` builds and
verifies the x64 executable and NSIS installer when the project is hosted in a
GitHub fork.

The presence of this `custom` directory automatically enables the custom build.

Brand asset provenance is recorded in [BRANDING.md](BRANDING.md).
