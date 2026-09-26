// 摄像头设置页
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../theme"
import "../../controls"

ScrollView {
    id: root
    clip: true

    // 测试状态
    property bool cameraTesting: false
    property string testResult: ""

    // 进入摄像头页时再扫描（启动阶段不扫 OpenCV，避免冷启动卡顿）
    Component.onCompleted: {
        if (settingsModel) {
            var list = settingsModel.getCameras()
            if (list.length === 0) {
                settingsModel.refreshCameras()
            } else {
                cameraCombo.model = list
                cameraCombo.currentIndex = settingsModel.selectedCameraIndex
            }
        }
    }

    Connections {
        target: settingsModel
        function onDevicesChanged() {
            if (settingsModel) {
                cameraCombo.model = settingsModel.getCameras()
                // model 设置后重新同步 currentIndex
                cameraCombo.currentIndex = settingsModel.selectedCameraIndex
            }
        }
        function onStatusMessage(message) {
            root.testResult = message
            if (message.startsWith("[OK]") || message.startsWith("[FAIL]") || message.startsWith("[ERROR]")) {
                root.cameraTesting = false
            }
        }
    }

    ColumnLayout {
        width: root.availableWidth
        spacing: Theme.spacingLg

        // 页面标题
        Text {
            text: "Camera Settings"
            font.pixelSize: Theme.fontSizeXl
            font.weight: Font.DemiBold
            color: Theme.textPrimary
        }

        // 设备选择
        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingMd

            Text {
                text: "Device Selection"
                font.pixelSize: Theme.fontSizeMd
                font.weight: Font.Medium
                color: Theme.textSecondary
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spacingMd

                Text {
                    text: "Camera"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textSecondary
                    Layout.preferredWidth: 80
                }

                XComboBox {
                    id: cameraCombo
                    Layout.fillWidth: true
                    currentIndex: settingsModel ? settingsModel.selectedCameraIndex : 0
                    onActivated: function(index) {
                        if (settingsModel) settingsModel.selectedCameraIndex = index
                    }
                    font.pixelSize: Theme.fontSizeSm
                }

                Button {
                    text: root.cameraTesting ? "Testing" : "Test"
                    enabled: !root.cameraTesting
                    Layout.preferredWidth: 70
                    Layout.preferredHeight: 32

                    background: Rectangle {
                        color: parent.enabled ? (parent.pressed ? Theme.primaryPressed : (parent.hovered ? Theme.primaryHover : Theme.primary)) : Theme.textPlaceholder
                        radius: Theme.radiusSm
                    }

                    contentItem: Text {
                        text: parent.text
                        font.pixelSize: Theme.fontSizeSm
                        color: "white"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }

                    onClicked: {
                        root.cameraTesting = true
                        root.testResult = ""
                        if (settingsModel) settingsModel.testCamera()
                    }
                }

                Button {
                    text: "Refresh"
                    Layout.preferredWidth: 70
                    Layout.preferredHeight: 32

                    background: Rectangle {
                        color: parent.pressed ? Theme.divider : (parent.hovered ? Theme.backgroundSecondary : Theme.backgroundHover)
                        radius: Theme.radiusSm
                    }

                    contentItem: Text {
                        text: parent.text
                        font.pixelSize: Theme.fontSizeSm
                        color: Theme.textSecondary
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }

                    onClicked: if (settingsModel) settingsModel.refreshCameras()
                }
            }
        }

        // 分隔线
        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: Theme.divider
        }

        // 视频参数
        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingMd

            Text {
                text: "Video Parameters"
                font.pixelSize: Theme.fontSizeMd
                font.weight: Font.Medium
                color: Theme.textSecondary
            }

            GridLayout {
                Layout.fillWidth: true
                columns: 2
                rowSpacing: Theme.spacingMd
                columnSpacing: Theme.spacingLg

                Text {
                    text: "Resolution"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textSecondary
                    Layout.preferredWidth: 80
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spacingSm

                    XSpinBox {
                        Layout.preferredWidth: 100
                        from: 320
                        to: 1920
                        stepSize: 160
                        value: settingsModel ? settingsModel.frameWidth : 640
                        onValueModified: if (settingsModel) settingsModel.frameWidth = value
                        font.pixelSize: Theme.fontSizeSm
                    }

                    Text {
                        text: "x"
                        font.pixelSize: Theme.fontSizeSm
                        color: Theme.textSecondary
                    }

                    XSpinBox {
                        Layout.preferredWidth: 100
                        from: 240
                        to: 1080
                        stepSize: 120
                        value: settingsModel ? settingsModel.frameHeight : 480
                        onValueModified: if (settingsModel) settingsModel.frameHeight = value
                        font.pixelSize: Theme.fontSizeSm
                    }
                }

                Text {
                    text: "Frame Rate"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textSecondary
                    Layout.preferredWidth: 80
                }

                RowLayout {
                    spacing: Theme.spacingSm

                    XSpinBox {
                        Layout.preferredWidth: 100
                        from: 10
                        to: 60
                        stepSize: 5
                        value: settingsModel ? settingsModel.fps : 30
                        onValueModified: if (settingsModel) settingsModel.fps = value
                    }

                    Text {
                        text: "FPS"
                        font.pixelSize: Theme.fontSizeSm
                        color: Theme.textSecondary
                    }
                }
            }
        }

        // 分隔线
        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: Theme.divider
        }

        // VL API 配置
        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingMd

            Text {
                text: "Vision Model (VL API)"
                font.pixelSize: Theme.fontSizeMd
                font.weight: Font.Medium
                color: Theme.textSecondary
            }

            GridLayout {
                Layout.fillWidth: true
                columns: 2
                rowSpacing: Theme.spacingMd
                columnSpacing: Theme.spacingLg

                Text {
                    text: "API URL"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textSecondary
                    Layout.preferredWidth: 80
                }
                TextField {
                    id: vlApiUrlField
                    Layout.fillWidth: true
                    text: settingsModel ? settingsModel.vlApiUrl : ""
                    onEditingFinished: if (settingsModel) settingsModel.vlApiUrl = text
                    placeholderText: "https://..."
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.inputText
                    background: Rectangle {
                        radius: Theme.radiusSm
                        color: Theme.backgroundSecondary
                        border.color: vlApiUrlField.activeFocus ? Theme.primary : "transparent"
                    }
                }

                Text {
                    text: "API Key"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textSecondary
                    Layout.preferredWidth: 80
                }
                XTextField {
                    id: vlApiKeyField
                    Layout.fillWidth: true
                    text: settingsModel ? settingsModel.vlApiKey : ""
                    onEditingFinished: if (settingsModel) settingsModel.vlApiKey = text
                    isPassword: true
                }

                Text {
                    text: "Model"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textSecondary
                    Layout.preferredWidth: 80
                }
                TextField {
                    id: vlModelsField
                    Layout.fillWidth: true
                    text: settingsModel ? settingsModel.vlModels : ""
                    onEditingFinished: if (settingsModel) settingsModel.vlModels = text
                    placeholderText: "glm-4v-plus"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.inputText
                    background: Rectangle {
                        radius: Theme.radiusSm
                        color: Theme.backgroundSecondary
                        border.color: vlModelsField.activeFocus ? Theme.primary : "transparent"
                    }
                }
            }
        }

        // 分隔线
        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: Theme.divider
        }

        // 视觉识别服务配置（无 VL API key 时使用）
        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingMd

            Text {
                text: "Vision Service (Q&A)"
                font.pixelSize: Theme.fontSizeMd
                font.weight: Font.Medium
                color: Theme.textSecondary
            }

            Text {
                text: "Used when no VL API key is configured. The server-provided address takes precedence."
                font.pixelSize: Theme.fontSizeSm
                color: Theme.textPlaceholder
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            GridLayout {
                Layout.fillWidth: true
                columns: 2
                rowSpacing: Theme.spacingMd
                columnSpacing: Theme.spacingLg

                Text {
                    text: "Explain URL"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textSecondary
                    Layout.preferredWidth: 80
                }
                TextField {
                    id: explainUrlField
                    Layout.fillWidth: true
                    text: settingsModel ? settingsModel.explainUrl : ""
                    onEditingFinished: if (settingsModel) settingsModel.explainUrl = text
                    placeholderText: "https://api.xiaozhi.me/vision/explain"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.inputText
                    background: Rectangle {
                        radius: Theme.radiusSm
                        color: Theme.backgroundSecondary
                        border.color: explainUrlField.activeFocus ? Theme.primary : "transparent"
                    }
                }

                Text {
                    text: "Token"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textSecondary
                    Layout.preferredWidth: 80
                }
                XTextField {
                    id: explainTokenField
                    Layout.fillWidth: true
                    text: settingsModel ? settingsModel.explainToken : ""
                    onEditingFinished: if (settingsModel) settingsModel.explainToken = text
                    isPassword: true
                }
            }
        }

        // 分隔线
        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: Theme.divider
        }

        // 测试结果
        Rectangle {
            Layout.fillWidth: true
            height: 48
            color: root.testResult.startsWith("[OK]") ? Theme.successLight :
                   root.testResult.startsWith("[FAIL]") ? Theme.errorLight :
                   root.testResult.startsWith("[ERROR]") ? Theme.errorLight : Theme.backgroundSecondary
            border.color: root.testResult.startsWith("[OK]") ? Theme.successBorder :
                          root.testResult.startsWith("[FAIL]") ? Theme.errorBorder :
                          root.testResult.startsWith("[ERROR]") ? Theme.errorBorder : Theme.divider
            radius: Theme.radiusMd
            visible: root.testResult.length > 0 || root.cameraTesting

            RowLayout {
                anchors.fill: parent
                anchors.margins: Theme.spacingMd
                spacing: Theme.spacingSm

                BusyIndicator {
                    visible: root.cameraTesting
                    running: root.cameraTesting
                    Layout.preferredWidth: 24
                    Layout.preferredHeight: 24
                }

                Text {
                    Layout.fillWidth: true
                    text: root.cameraTesting ? "Testing camera..." : root.testResult
                    font.pixelSize: Theme.fontSizeSm
                    color: root.testResult.startsWith("[OK]") ? Theme.success :
                           root.testResult.startsWith("[FAIL]") ? Theme.error :
                           root.testResult.startsWith("[ERROR]") ? Theme.error : Theme.textSecondary
                    elide: Text.ElideRight
                }
            }
        }

        // 提示信息
        Text {
            Layout.fillWidth: true
            text: "The camera is used for visual recognition. To use a local VL model, configure the API URL and key."
            font.pixelSize: Theme.fontSizeSm
            color: Theme.textPlaceholder
            wrapMode: Text.WordWrap
        }

        Item { Layout.fillHeight: true }
    }
}
