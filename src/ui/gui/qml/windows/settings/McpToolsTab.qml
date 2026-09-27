// MCP 工具启用：分组 + 单工具开关（黑名单 MCP_TOOLS.DISABLED）
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../theme"
import "../../controls"

ScrollView {
    id: root
    clip: true
    // 给纵向滚动条留空，避免右侧开关被裁切
    rightPadding: 8
    contentWidth: availableWidth

    property var catalog: []
    property bool mqttScanBusy: false
    property string mqttScanResult: ""
    property var mqttScanMatches: []

    function reloadCatalog() {
        if (!settingsModel) {
            catalog = []
            return
        }
        try {
            catalog = JSON.parse(settingsModel.mcpToolsCatalogJson || "[]")
        } catch (e) {
            catalog = []
        }
    }

    function groupsModel() {
        var map = ({})
        var order = []
        for (var i = 0; i < catalog.length; i++) {
            var row = catalog[i]
            var g = row.group || "other"
            if (!map[g]) {
                map[g] = {
                    group: g,
                    groupLabel: row.groupLabel || g,
                    tools: []
                }
                order.push(g)
            }
            map[g].tools.push(row)
        }
        var out = []
        for (var j = 0; j < order.length; j++)
            out.push(map[order[j]])
        return out
    }

    function groupEnabledCount(tools) {
        var n = 0
        for (var i = 0; i < tools.length; i++)
            if (tools[i].enabled)
                n++
        return n
    }

    function groupStatusText(tools) {
        var on = groupEnabledCount(tools)
        var total = tools.length
        // 用数量，避免与按钮「全开/全关」文案重复
        return on + "/" + total
    }

    component GroupActionButton: XButton {
        id: btn
        textSize: Theme.fontSizeXs
        Layout.preferredHeight: 28
        Layout.preferredWidth: 52
        padding: 0
        variant: "secondary"
    }

    Component.onCompleted: reloadCatalog()

    Connections {
        target: settingsModel
        function onSettingsChanged() { root.reloadCatalog() }
        function onMqttBrokerScanFinished(result) {
            root.mqttScanBusy = false
            root.mqttScanMatches = []
            try { root.mqttScanMatches = JSON.parse(result) } catch (e) {}
            root.mqttScanResult = root.mqttScanMatches.length
                ? "Open hosts on port " + settingsModel.smartHomePort + ":"
                : "No open host found on port " + settingsModel.smartHomePort
        }
    }

    ColumnLayout {
        width: root.availableWidth
        spacing: Theme.spacingLg

        Text {
            text: "MCP Tools"
            font.pixelSize: Theme.fontSizeXl
            font.weight: Font.DemiBold
            color: Theme.textPrimary
        }

        Text {
            Layout.fillWidth: true
            text: "Controls which tools are exposed to the server. When disabled, a tool does not appear in tools/list and cannot be called. After saving, if connected, it reconnects automatically to update the list."
            font.pixelSize: Theme.fontSizeXs
            color: Theme.textSecondary
            wrapMode: Text.WordWrap
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSm

            Text {
                text: "Smart Home MQTT"
                font.pixelSize: Theme.fontSizeMd
                font.weight: Font.Medium
                color: Theme.textSecondary
            }
            Text {
                Layout.fillWidth: true
                text: "Configure the broker used by the enabled smart-home MCP tools. The existing tool switches control whether smart home is enabled."
                font.pixelSize: Theme.fontSizeXs
                color: Theme.textPlaceholder
                wrapMode: Text.WordWrap
            }

            GridLayout {
                Layout.fillWidth: true
                columns: 2
                rowSpacing: Theme.spacingSm
                columnSpacing: Theme.spacingMd

                Text { text: "Broker IP / Host"; font.pixelSize: Theme.fontSizeSm; color: Theme.textSecondary }
                XTextField {
                    Layout.fillWidth: true
                    text: settingsModel ? settingsModel.smartHomeBroker : ""
                    placeholderText: "192.168.1.10"
                    onEditingFinished: if (settingsModel) settingsModel.smartHomeBroker = text
                }

                Text { text: "Port"; font.pixelSize: Theme.fontSizeSm; color: Theme.textSecondary }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spacingSm
                    TextField {
                        id: mqttPortField
                        Layout.preferredWidth: 100
                        text: settingsModel ? String(settingsModel.smartHomePort) : "1883"
                        placeholderText: "1883"
                        validator: IntValidator { bottom: 1; top: 65535 }
                        font.pixelSize: Theme.fontSizeSm
                        color: Theme.inputText
                        onEditingFinished: if (settingsModel && acceptableInput) settingsModel.smartHomePort = Number(text)
                        background: Rectangle {
                            radius: Theme.radiusSm
                            color: Theme.backgroundSecondary
                            border.color: Theme.border
                        }
                    }
                    XButton {
                        Layout.preferredWidth: 110
                        Layout.preferredHeight: 32
                        textSize: Theme.fontSizeSm
                        variant: "secondary"
                        text: root.mqttScanBusy ? "Scanning..." : "Scan"
                        enabled: !root.mqttScanBusy
                        onClicked: {
                            root.mqttScanBusy = true
                            root.mqttScanResult = ""
                            if (settingsModel) settingsModel.scanMqttBroker()
                            else root.mqttScanBusy = false
                        }
                    }
                }

                Text { text: "Username"; font.pixelSize: Theme.fontSizeSm; color: Theme.textSecondary }
                XTextField {
                    Layout.fillWidth: true
                    text: settingsModel ? settingsModel.smartHomeUsername : ""
                    onEditingFinished: if (settingsModel) settingsModel.smartHomeUsername = text
                }

                Text { text: "Password"; font.pixelSize: Theme.fontSizeSm; color: Theme.textSecondary }
                XTextField {
                    Layout.fillWidth: true
                    isPassword: true
                    text: settingsModel ? settingsModel.smartHomePassword : ""
                    onEditingFinished: if (settingsModel) settingsModel.smartHomePassword = text
                }
            }

            Text {
                Layout.fillWidth: true
                text: root.mqttScanResult
                visible: text.length > 0
                font.pixelSize: Theme.fontSizeXs
                color: root.mqttScanResult.startsWith("Open hosts") ? Theme.success : Theme.textPlaceholder
                wrapMode: Text.WordWrap
            }
            Text {
                Layout.fillWidth: true
                visible: root.mqttScanMatches.length > 0
                text: root.mqttScanMatches.map(function(host) {
                    return host + ":" + settingsModel.smartHomePort
                }).join(", ")
                font.pixelSize: Theme.fontSizeXs
                color: Theme.textSecondary
                wrapMode: Text.WordWrap
            }
        }

        Repeater {
            model: root.groupsModel()

            delegate: ColumnLayout {
                id: groupBlock
                required property var modelData
                Layout.fillWidth: true
                spacing: Theme.spacingSm

                // 组头
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spacingSm

                    Text {
                        text: groupBlock.modelData.groupLabel
                        font.pixelSize: Theme.fontSizeMd
                        font.weight: Font.Medium
                        color: Theme.textSecondary
                    }

                    Text {
                        text: root.groupStatusText(groupBlock.modelData.tools)
                        font.pixelSize: Theme.fontSizeXs
                        color: Theme.textPlaceholder
                    }

                    Item { Layout.fillWidth: true }

                    GroupActionButton {
                        text: "All On"
                        onClicked: {
                            if (settingsModel)
                                settingsModel.setMcpToolGroupEnabled(groupBlock.modelData.group, true)
                        }
                    }
                    GroupActionButton {
                        text: "All Off"
                        onClicked: {
                            if (settingsModel)
                                settingsModel.setMcpToolGroupEnabled(groupBlock.modelData.group, false)
                        }
                    }
                }

                // 工具列表卡片
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: toolsCol.implicitHeight + Theme.spacingSm * 2
                    radius: Theme.radiusMd
                    color: Theme.backgroundSecondary
                    border.width: 1
                    border.color: Theme.divider

                    ColumnLayout {
                        id: toolsCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: Theme.spacingSm
                        spacing: 0

                        Repeater {
                            model: groupBlock.modelData.tools

                            delegate: ColumnLayout {
                                id: toolRow
                                required property var modelData
                                required property int index
                                Layout.fillWidth: true
                                spacing: 0

                                RowLayout {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 48
                                    Layout.leftMargin: Theme.spacingSm
                                    Layout.rightMargin: Theme.spacingSm
                                    spacing: Theme.spacingMd

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 2

                                        Text {
                                            text: toolRow.modelData.label || toolRow.modelData.name
                                            font.pixelSize: Theme.fontSizeSm
                                            color: Theme.textPrimary
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                        Text {
                                            text: toolRow.modelData.name
                                            font.pixelSize: Theme.fontSizeXs
                                            color: Theme.textPlaceholder
                                            elide: Text.ElideMiddle
                                            Layout.fillWidth: true
                                        }
                                    }

                                    // 固定开关区域，避免被挤出/裁切
                                    Item {
                                        Layout.preferredWidth: 52
                                        Layout.preferredHeight: 28
                                        Layout.alignment: Qt.AlignVCenter

                                        XSwitch {
                                            anchors.centerIn: parent
                                            // 无文字时避免 contentItem 额外占位
                                            text: ""
                                            checked: toolRow.modelData.enabled
                                            onToggled: {
                                                if (settingsModel)
                                                    settingsModel.setMcpToolEnabled(
                                                        toolRow.modelData.name, checked)
                                            }
                                        }
                                    }
                                }

                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.leftMargin: Theme.spacingSm
                                    Layout.rightMargin: Theme.spacingSm
                                    height: 1
                                    color: Theme.divider
                                    visible: toolRow.index < groupBlock.modelData.tools.length - 1
                                }
                            }
                        }
                    }
                }
            }
        }

        Item { Layout.preferredHeight: Theme.spacingMd }
    }
}
