message(STATUS "FlyCam: Adding AeroScope/AgroScope custom build")

list(APPEND CUSTOM_RESOURCES
    ${CMAKE_CURRENT_SOURCE_DIR}/custom.qrc
)
set(QGC_RESOURCES ${QGC_RESOURCES} ${CUSTOM_RESOURCES} CACHE STRING "Paths to .qrc resources" FORCE)

set(CUSTOM_SOURCES
    ${CMAKE_CURRENT_SOURCE_DIR}/src/CargoBayController.cc
    ${CMAKE_CURRENT_SOURCE_DIR}/src/CargoBayController.h
    ${CMAKE_CURRENT_SOURCE_DIR}/src/DispatcherClient.cc
    ${CMAKE_CURRENT_SOURCE_DIR}/src/DispatcherClient.h
    ${CMAKE_CURRENT_SOURCE_DIR}/src/FlyCamPlugin.cc
    ${CMAKE_CURRENT_SOURCE_DIR}/src/FlyCamPlugin.h
    ${CMAKE_CURRENT_SOURCE_DIR}/src/FlyCamSplashScreen.cc
    ${CMAKE_CURRENT_SOURCE_DIR}/src/FlyCamSplashScreen.h
    CACHE INTERNAL "" FORCE
)

set(CUSTOM_INCLUDE_DIRECTORIES
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    CACHE INTERNAL "" FORCE
)

set(CUSTOM_DEFINITIONS
    QGC_CUSTOM_BUILD
    CUSTOMHEADER="FlyCamPlugin.h"
    CUSTOMCLASS=FlyCamPlugin
    CACHE INTERNAL "" FORCE
)

set(CUSTOM_QT_COMPONENTS
    Core
    Network
    Qml
    CACHE INTERNAL "" FORCE
)

set(FLYCAM_DISPATCHER_SERVER_EXECUTABLE "" CACHE FILEPATH
    "Prebuilt FlyCam dispatcher server executable to include in the installer"
)
if(WIN32 AND FLYCAM_DISPATCHER_SERVER_EXECUTABLE)
    if(EXISTS "${FLYCAM_DISPATCHER_SERVER_EXECUTABLE}")
        install(PROGRAMS "${FLYCAM_DISPATCHER_SERVER_EXECUTABLE}"
            DESTINATION "${CMAKE_INSTALL_BINDIR}"
        )
        message(STATUS "FlyCam: Bundling dispatcher server: ${FLYCAM_DISPATCHER_SERVER_EXECUTABLE}")
    else()
        message(FATAL_ERROR
            "FlyCam dispatcher server executable not found: ${FLYCAM_DISPATCHER_SERVER_EXECUTABLE}"
        )
    endif()
endif()

set(FLYCAM_VIDEO_ANALYTICS_EXECUTABLE "" CACHE FILEPATH
    "Prebuilt FlyCam video analytics executable to include in the installer"
)
if(WIN32 AND FLYCAM_VIDEO_ANALYTICS_EXECUTABLE)
    if(EXISTS "${FLYCAM_VIDEO_ANALYTICS_EXECUTABLE}")
        install(PROGRAMS "${FLYCAM_VIDEO_ANALYTICS_EXECUTABLE}"
            DESTINATION "${CMAKE_INSTALL_BINDIR}"
        )
        message(STATUS "FlyCam: Bundling video analytics: ${FLYCAM_VIDEO_ANALYTICS_EXECUTABLE}")
    else()
        message(FATAL_ERROR
            "FlyCam video analytics executable not found: ${FLYCAM_VIDEO_ANALYTICS_EXECUTABLE}"
        )
    endif()
endif()

if(WIN32)
    install(FILES
        "${CMAKE_SOURCE_DIR}/analytics/README_RU.md"
        "${CMAKE_SOURCE_DIR}/docs/certification/SECURITY_PROFILE_RU.md"
        "${CMAKE_SOURCE_DIR}/docs/certification/VERIFICATION_PLAN_RU.md"
        "${CMAKE_SOURCE_DIR}/docs/certification/TRACEABILITY_MATRIX_RU.md"
        DESTINATION "${CMAKE_INSTALL_DATADIR}/FlyCam/docs"
    )
endif()
