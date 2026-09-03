# Product identity
set(QGC_APP_NAME "FlyCam-AeroScope-AgroScope-Control-Center" CACHE STRING "Application name" FORCE)
set(QGC_APP_DESCRIPTION "Satbayev University / FlyCam AeroScope/AgroScope multi-vehicle ground control station for PX4 UAVs" CACHE STRING "Application description" FORCE)
set(QGC_APP_COPYRIGHT "Copyright (c) 2026 Satbayev University and FlyCam" CACHE STRING "Copyright notice" FORCE)
set(QGC_ORG_NAME "Satbayev University and FlyCam" CACHE STRING "Organization name" FORCE)
set(QGC_ORG_DOMAIN "satbayev.university" CACHE STRING "Organization domain" FORCE)
set(QGC_PACKAGE_NAME "kz.satbayev.flycam.aeroscope.agroscope.controlcenter" CACHE STRING "Package identifier" FORCE)

# First release targets Cube Orange + PX4 only.
set(QGC_DISABLE_APM_PLUGIN_FACTORY ON CACHE BOOL "Disable ArduPilot firmware UI" FORCE)

# Camera support is intentionally excluded from the first release.
set(QGC_ENABLE_GST_VIDEOSTREAMING OFF CACHE BOOL "Disable video backend" FORCE)

# Windows branding
set(QGC_WINDOWS_ICON_PATH
    "${CMAKE_SOURCE_DIR}/${QGC_CUSTOM_DIR}/deploy/windows/FlyCamControlCenter.ico"
    CACHE FILEPATH "Windows application icon" FORCE
)
