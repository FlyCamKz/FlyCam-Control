import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

import QGroundControl
import QGroundControl.Controls

import FlyCam.Core

Item {
    id: root

    required property var parentToolInsets
    required property var mapControl

    property var totalToolInsets: totalInsets
    property real toolsMargin: ScreenTools.defaultFontPixelWidth * 0.75

    QGCToolInsets {
        id: totalInsets

        leftEdgeTopInset: parentToolInsets.leftEdgeTopInset
        leftEdgeCenterInset: cargoPanel.visible
            ? Math.max(parentToolInsets.leftEdgeCenterInset, cargoPanel.x + cargoPanel.width)
            : parentToolInsets.leftEdgeCenterInset
        leftEdgeBottomInset: parentToolInsets.leftEdgeBottomInset
        rightEdgeTopInset: parentToolInsets.rightEdgeTopInset
        rightEdgeCenterInset: parentToolInsets.rightEdgeCenterInset
        rightEdgeBottomInset: parentToolInsets.rightEdgeBottomInset
        topEdgeLeftInset: parentToolInsets.topEdgeLeftInset
        topEdgeCenterInset: parentToolInsets.topEdgeCenterInset
        topEdgeRightInset: parentToolInsets.topEdgeRightInset
        bottomEdgeLeftInset: parentToolInsets.bottomEdgeLeftInset
        bottomEdgeCenterInset: parentToolInsets.bottomEdgeCenterInset
        bottomEdgeRightInset: parentToolInsets.bottomEdgeRightInset
    }

    Rectangle {
        id: cargoPanel

        anchors.left: parent.left
        anchors.top: parent.top
        anchors.leftMargin: parentToolInsets.leftEdgeTopInset + root.toolsMargin
        anchors.topMargin: parentToolInsets.topEdgeLeftInset + root.toolsMargin
        width: ScreenTools.defaultFontPixelWidth * 31
        height: cargoLayout.implicitHeight + root.toolsMargin * 2
        color: qgcPal.window
        border.color: qgcPal.buttonBorder
        border.width: 1
        radius: ScreenTools.defaultBorderRadius
        z: QGroundControl.zOrderWidgets

        property bool expanded: true

        QGCPalette {
            id: qgcPal
            colorGroupEnabled: true
        }

        ColumnLayout {
            id: cargoLayout

            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: root.toolsMargin
            spacing: ScreenTools.defaultFontPixelHeight * 0.45

            RowLayout {
                Layout.fillWidth: true

                Rectangle {
                    Layout.preferredWidth: ScreenTools.defaultFontPixelWidth * 1.5
                    Layout.preferredHeight: width
                    radius: width / 2
                    color: CargoBay.commandInProgress
                        ? qgcPal.colorOrange
                        : (CargoBay.openAllowed ? qgcPal.colorGreen : qgcPal.colorRed)
                }

                QGCLabel {
                    Layout.fillWidth: true
                    text: qsTr("ГРУЗОВОЙ ОТСЕК")
                    font.bold: true
                }

                QGCButton {
                    text: cargoPanel.expanded ? "−" : "+"
                    onClicked: cargoPanel.expanded = !cargoPanel.expanded
                }
            }

            QGCLabel {
                Layout.fillWidth: true
                visible: cargoPanel.expanded
                text: CargoBay.statusText
                wrapMode: Text.WordWrap
                font.pointSize: ScreenTools.smallFontPointSize
            }

            QGCLabel {
                Layout.fillWidth: true
                visible: cargoPanel.expanded && CargoBay.commandedStateKnown
                text: CargoBay.commandedOpen
                    ? qsTr("Командное состояние: ОТКРЫТ")
                    : qsTr("Командное состояние: ЗАКРЫТ")
                color: CargoBay.commandedOpen ? qgcPal.colorOrange : qgcPal.colorGreen
                font.bold: CargoBay.commandedOpen
            }

            RowLayout {
                Layout.fillWidth: true
                visible: cargoPanel.expanded
                spacing: ScreenTools.defaultFontPixelWidth

                QGCButton {
                    Layout.fillWidth: true
                    text: qsTr("Открыть")
                    enabled: CargoBay.openAllowed
                    backgroundColor: qgcPal.colorOrange
                    textColor: qgcPal.primaryButtonText
                    onClicked: openConfirmationFactory.open()
                }

                QGCButton {
                    Layout.fillWidth: true
                    text: qsTr("Закрыть")
                    enabled: CargoBay.closeAllowed
                    primary: true
                    onClicked: CargoBay.requestClose()
                }
            }

            RowLayout {
                Layout.fillWidth: true
                visible: cargoPanel.expanded

                QGCLabel {
                    Layout.fillWidth: true
                    text: qsTr("Без датчика положения")
                    color: qgcPal.warningText
                    font.pointSize: ScreenTools.smallFontPointSize
                }

                QGCButton {
                    text: qsTr("Настройка")
                    enabled: !CargoBay.commandInProgress
                    onClicked: settingsFactory.open()
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                visible: cargoPanel.expanded
                color: qgcPal.buttonBorder
            }

            RowLayout {
                Layout.fillWidth: true
                visible: cargoPanel.expanded

                Rectangle {
                    Layout.preferredWidth: ScreenTools.defaultFontPixelWidth * 1.2
                    Layout.preferredHeight: width
                    radius: width / 2
                    color: Dispatcher.serverReachable ? qgcPal.colorGreen : qgcPal.colorOrange
                }

                QGCLabel {
                    Layout.fillWidth: true
                    text: Dispatcher.statusText
                    wrapMode: Text.WordWrap
                    font.pointSize: ScreenTools.smallFontPointSize
                }

                QGCButton {
                    text: qsTr("Сервер")
                    onClicked: dispatcherSettingsFactory.open()
                }
            }
        }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.bottom: parent.bottom
        anchors.leftMargin: root.toolsMargin
        anchors.bottomMargin: root.toolsMargin
        width: ScreenTools.defaultFontPixelWidth * 31
        height: ScreenTools.defaultFontPixelHeight * 4
        color: qgcPal.window
        opacity: 0.94
        radius: ScreenTools.defaultBorderRadius

        Image {
            anchors.fill: parent
            anchors.margins: ScreenTools.defaultFontPixelWidth * 0.75
            source: "qrc:/flycam/branding/brand_lockup.png"
            fillMode: Image.PreserveAspectFit
            mipmap: true
        }
    }

    QGCPopupDialogFactory {
        id: openConfirmationFactory
        dialogComponent: openConfirmationComponent
    }

    Component {
        id: openConfirmationComponent

        QGCPopupDialog {
            title: qsTr("Подтверждение открытия")
            buttons: Dialog.Ok | Dialog.Cancel
            acceptButtonEnabled: CargoBay.openAllowed
            onAccepted: CargoBay.requestOpen()

            ColumnLayout {
                width: ScreenTools.defaultFontPixelWidth * 52
                spacing: ScreenTools.defaultFontPixelHeight

                QGCLabel {
                    Layout.fillWidth: true
                    text: qsTr("Убедитесь, что БПЛА находится на земле и винты остановлены. ")
                        + qsTr("Зона под отсеком должна быть свободна.")
                    wrapMode: Text.WordWrap
                }

                QGCLabel {
                    Layout.fillWidth: true
                    text: qsTr("Открытие заблокировано при вооружённом состоянии или в полёте.")
                    color: qgcPal.warningText
                    wrapMode: Text.WordWrap
                    font.bold: true
                }
            }
        }
    }

    QGCPopupDialogFactory {
        id: settingsFactory
        dialogComponent: settingsComponent
    }

    Component {
        id: settingsComponent

        QGCPopupDialog {
            id: settingsDialog

            title: qsTr("Настройка Futaba S3001")
            buttons: Dialog.Ok | Dialog.Cancel

            property int selectedActuatorNumber: CargoBay.actuatorNumber
            property real selectedOpenValue: CargoBay.openValue
            property real selectedClosedValue: CargoBay.closedValue

            onAccepted: {
                CargoBay.actuatorNumber = selectedActuatorNumber
                CargoBay.openValue = selectedOpenValue
                CargoBay.closedValue = selectedClosedValue
            }

            GridLayout {
                columns: 2
                rowSpacing: ScreenTools.defaultFontPixelHeight
                columnSpacing: ScreenTools.defaultFontPixelWidth * 2

                QGCLabel { text: qsTr("Функция Actuator Set") }
                SpinBox {
                    from: 1
                    to: 6
                    value: settingsDialog.selectedActuatorNumber
                    onValueModified: settingsDialog.selectedActuatorNumber = value
                }

                QGCLabel { text: qsTr("Открыто") }
                RowLayout {
                    Slider {
                        Layout.preferredWidth: ScreenTools.defaultFontPixelWidth * 28
                        from: -1.0
                        to: 1.0
                        stepSize: 0.05
                        value: settingsDialog.selectedOpenValue
                        onMoved: settingsDialog.selectedOpenValue = value
                    }
                    QGCLabel { text: settingsDialog.selectedOpenValue.toFixed(2) }
                }

                QGCLabel { text: qsTr("Закрыто") }
                RowLayout {
                    Slider {
                        Layout.preferredWidth: ScreenTools.defaultFontPixelWidth * 28
                        from: -1.0
                        to: 1.0
                        stepSize: 0.05
                        value: settingsDialog.selectedClosedValue
                        onMoved: settingsDialog.selectedClosedValue = value
                    }
                    QGCLabel { text: settingsDialog.selectedClosedValue.toFixed(2) }
                }

                QGCLabel {
                    Layout.columnSpan: 2
                    Layout.preferredWidth: ScreenTools.defaultFontPixelWidth * 52
                    text: qsTr("Для AUX1 с функцией ‘Peripheral via Actuator Set 1’ выберите значение 1. ")
                        + qsTr("Начинайте проверку при снятых винтах.")
                    wrapMode: Text.WordWrap
                    color: qgcPal.warningText
                }
            }
        }
    }

    QGCPopupDialogFactory {
        id: dispatcherSettingsFactory
        dialogComponent: dispatcherSettingsComponent
    }

    Component {
        id: dispatcherSettingsComponent

        QGCPopupDialog {
            id: dispatcherDialog

            title: qsTr("Диспетчерский сервер")
            buttons: Dialog.Ok | Dialog.Cancel

            property bool selectedEnabled: Dispatcher.enabled
            property string selectedServerUrl: Dispatcher.serverUrl
            property string selectedApiKey: Dispatcher.apiKey

            onAccepted: {
                Dispatcher.serverUrl = selectedServerUrl
                Dispatcher.apiKey = selectedApiKey
                Dispatcher.enabled = selectedEnabled
                Dispatcher.sendNow()
            }

            GridLayout {
                columns: 2
                rowSpacing: ScreenTools.defaultFontPixelHeight
                columnSpacing: ScreenTools.defaultFontPixelWidth * 2

                QGCLabel { text: qsTr("Передача телеметрии") }
                QGCCheckBox {
                    checked: dispatcherDialog.selectedEnabled
                    onClicked: dispatcherDialog.selectedEnabled = checked
                }

                QGCLabel { text: qsTr("Адрес сервера") }
                QGCTextField {
                    Layout.preferredWidth: ScreenTools.defaultFontPixelWidth * 42
                    text: dispatcherDialog.selectedServerUrl
                    placeholderText: "http://127.0.0.1:8088"
                    onTextChanged: dispatcherDialog.selectedServerUrl = text
                }

                QGCLabel { text: qsTr("API-ключ") }
                QGCTextField {
                    Layout.preferredWidth: ScreenTools.defaultFontPixelWidth * 42
                    text: dispatcherDialog.selectedApiKey
                    echoMode: TextInput.Password
                    placeholderText: qsTr("Необязательно для локального сервера")
                    onTextChanged: dispatcherDialog.selectedApiKey = text
                }

                QGCLabel {
                    Layout.columnSpan: 2
                    Layout.preferredWidth: ScreenTools.defaultFontPixelWidth * 54
                    text: qsTr("Сервер получает только телеметрию и события. ")
                        + qsTr("Удалённые команды полёта и открытия отсека отключены.")
                    wrapMode: Text.WordWrap
                    color: qgcPal.warningText
                }
            }
        }
    }
}
