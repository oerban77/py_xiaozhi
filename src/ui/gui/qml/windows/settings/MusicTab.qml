// 音乐设置页
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../theme"
import "../../controls"

ScrollView {
    id: root
    clip: true

    ColumnLayout {
        width: root.availableWidth
        spacing: Theme.spacingLg

        Text {
            text: "Music Configuration"
            font.pixelSize: Theme.fontSizeXl
            font.weight: Font.DemiBold
            color: Theme.textPrimary
        }

        // API 配置
        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingMd

            Text {
                text: "API Settings"
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
                    text: "Search API"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textSecondary
                    Layout.preferredWidth: 130
                }
                TextField {
                    id: musicSearchUrlField
                    Layout.fillWidth: true
                    text: settingsModel ? settingsModel.musicSearchUrl : ""
                    // 边改边写回；只靠 editingFinished 时点保存常写不上空串
                    onTextEdited: if (settingsModel) settingsModel.musicSearchUrl = text
                    onEditingFinished: if (settingsModel) settingsModel.musicSearchUrl = text
                    placeholderText: "Leave empty to use the default Kuwo search API"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.inputText
                    background: Rectangle {
                        radius: Theme.radiusSm
                        color: Theme.backgroundSecondary
                        border.color: musicSearchUrlField.activeFocus ? Theme.primary : "transparent"
                    }
                }

                Text {
                    text: "Direct Link API"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textSecondary
                    Layout.preferredWidth: 130
                }
                TextField {
                    id: musicUrlApiField
                    Layout.fillWidth: true
                    text: settingsModel ? settingsModel.musicUrlApi : ""
                    onTextEdited: if (settingsModel) settingsModel.musicUrlApi = text
                    onEditingFinished: if (settingsModel) settingsModel.musicUrlApi = text
                    placeholderText: "Leave empty to use the default lx-music-api"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.inputText
                    background: Rectangle {
                        radius: Theme.radiusSm
                        color: Theme.backgroundSecondary
                        border.color: musicUrlApiField.activeFocus ? Theme.primary : "transparent"
                    }
                }

                Text {
                    text: "API Key"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textSecondary
                    Layout.preferredWidth: 120
                }
                TextField {
                    id: musicUrlApiKeyField
                    Layout.fillWidth: true
                    text: settingsModel ? settingsModel.musicUrlApiKey : ""
                    onTextEdited: if (settingsModel) settingsModel.musicUrlApiKey = text
                    onEditingFinished: if (settingsModel) settingsModel.musicUrlApiKey = text
                    placeholderText: "Leave empty to use the default key"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.inputText
                    background: Rectangle {
                        radius: Theme.radiusSm
                        color: Theme.backgroundSecondary
                        border.color: musicUrlApiKeyField.activeFocus ? Theme.primary : "transparent"
                    }
                }

                Text {
                    text: "Opus Catalog URL"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textSecondary
                    Layout.preferredWidth: 130
                }
                TextField {
                    id: musicOpusCatalogUrlField
                    Layout.fillWidth: true
                    text: settingsModel ? settingsModel.musicOpusCatalogUrl : ""
                    onTextEdited: if (settingsModel) settingsModel.musicOpusCatalogUrl = text
                    onEditingFinished: if (settingsModel) settingsModel.musicOpusCatalogUrl = text
                    placeholderText: "Leave empty to use the default catalog"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.inputText
                    background: Rectangle {
                        radius: Theme.radiusSm
                        color: Theme.backgroundSecondary
                        border.color: musicOpusCatalogUrlField.activeFocus ? Theme.primary : "transparent"
                    }
                }

                Text {
                    text: "Opus Stream Base"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textSecondary
                    Layout.preferredWidth: 130
                }
                TextField {
                    id: musicOpusStreamBaseField
                    Layout.fillWidth: true
                    text: settingsModel ? settingsModel.musicOpusStreamBase : ""
                    onTextEdited: if (settingsModel) settingsModel.musicOpusStreamBase = text
                    onEditingFinished: if (settingsModel) settingsModel.musicOpusStreamBase = text
                    placeholderText: "Leave empty to use the default stream base"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.inputText
                    background: Rectangle {
                        radius: Theme.radiusSm
                        color: Theme.backgroundSecondary
                        border.color: musicOpusStreamBaseField.activeFocus ? Theme.primary : "transparent"
                    }
                }
            }

            Text {
                text: "The search API uses the official Kuwo endpoint; the direct link API fetches playback URLs (requires an API key). The Opus catalog provides the online song list used by the list/search/play tools."
                font.pixelSize: Theme.fontSizeXs
                color: Theme.textPlaceholder
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }

        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: Theme.divider
        }

        // 播放偏好
        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingMd

            Text {
                text: "Playback Preferences"
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
                    text: "Default Quality"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textSecondary
                    Layout.preferredWidth: 120
                }
                XComboBox {
                    id: musicQualityCombo
                    Layout.preferredWidth: 150
                    model: ["128k", "320k"]
                    currentIndex: {
                        var q = settingsModel ? settingsModel.musicDefaultQuality : "320k"
                        var idx = ["128k", "320k"].indexOf(q)
                        return idx >= 0 ? idx : 1
                    }
                    onActivated: function(index) {
                        if (settingsModel) settingsModel.musicDefaultQuality = model[index]
                    }
                    font.pixelSize: Theme.fontSizeSm
                }

                Text {
                    text: "Music Volume"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textSecondary
                    Layout.preferredWidth: 120
                }
                TextField {
                    id: musicVolumeField
                    Layout.preferredWidth: 150
                    text: settingsModel ? String(settingsModel.musicVolume) : "100"
                    validator: IntValidator { bottom: 0; top: 200 }
                    onEditingFinished: {
                        if (settingsModel) settingsModel.musicVolume = Number(text || 100)
                    }
                    onTextEdited: {
                        if (settingsModel) settingsModel.musicVolume = Number(text || 100)
                    }
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.inputText
                    background: Rectangle {
                        radius: Theme.radiusSm
                        color: Theme.backgroundSecondary
                        border.color: musicVolumeField.activeFocus ? Theme.primary : "transparent"
                    }
                }
            }
        }

        Item { Layout.fillHeight: true }
    }
}
