// 主窗口 - 匹配原布局
import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "../theme"
import "../components"
import "../controls"

AppWindow {
    id: root

    width: 420
    height: 520
    minimumWidth: 360
    minimumHeight: 420
    title: ""
    // 由 QmlAppHost.show_root 控制显示；避免 QML 加载瞬间抢焦点
    visible: false
    property string attachmentPath: ""
    property string attachmentName: ""
    property string attachmentStatus: ""
    property bool attachmentUseDocument: false

    FileDialog {
        id: attachmentDialog
        title: "Attach a document or image"
        fileMode: FileDialog.OpenFile
        nameFilters: [
            "Documents and images (*.txt *.md *.json *.csv *.log *.ini *.yaml *.yml *.xml *.html *.htm *.doc *.docx *.xls *.xlsx *.ppt *.pptx *.odt *.ods *.odp *.pdf *.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff *.ico)",
            "All files (*)"
        ]

        onAccepted: {
            const fileUrl = String(selectedFile)
            root.attachmentPath = fileUrl
            root.attachmentName = decodeURIComponent(
                fileUrl.substring(fileUrl.lastIndexOf("/") + 1)
            )
            root.attachmentUseDocument = false
            root.attachmentStatus = ""
        }
    }

    Connections {
        target: eventBridge

        function onAttachmentStatusChanged(status) {
            root.attachmentStatus = status
            attachmentStatusTimer.restart()
        }
    }

    Timer {
        id: attachmentStatusTimer
        interval: 4000
        onTriggered: root.attachmentStatus = ""
    }

    // 直接使用 ColumnLayout，不需要额外的 Rectangle 层
    // AppWindow 已经提供了带圆角的容器
    ColumnLayout {
        anchors.fill: parent
        spacing: 0

            // 自定义标题栏 - 平台自适应
            TitleBar {
                Layout.fillWidth: true
                showMaximize: true
                onMinimizeClicked: root.showMinimized()
                onMaximizeClicked: {
                    if (root.visibility === Window.FullScreen || root.visibility === Window.Maximized) {
                        root.showNormal()
                    } else {
                        root.showMaximized()
                    }
                }
                onCloseClicked: {
                    if (eventBridge) eventBridge.onQuitRequest()
                }
            }

            // 状态卡片区域
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: "transparent"

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: Theme.spacingMd
                    spacing: Theme.spacingMd

                    // 状态标签
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 40
                        color: Theme.primaryLight
                        radius: Theme.radiusMd

                        Text {
                            anchors.centerIn: parent
                            text: (mainModel && mainModel.statusText) ? mainModel.statusText : "Idle"
                            font.pixelSize: Theme.fontSizeMd
                            font.weight: Font.Bold
                            color: Theme.primaryText
                        }
                    }

                    // 表情显示区域
                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: 80

                        property string currentEmotionUrl: (mainModel && mainModel.emotionUrl) ? mainModel.emotionUrl : ""

                        // 静音/取消静音按钮（右上角）
                        XIconButton {
                            id: muteBtn
                            anchors.top: parent.top
                            anchors.right: parent.right
                            width: 32
                            height: 32
                            flat: true
                            icon: (mainModel && mainModel.muted) ? "🔇" : "🔊"
                            iconColor: (mainModel && mainModel.muted) ? Theme.error : Theme.textSecondary
                            iconHoverColor: iconColor

                            ToolTip.visible: hovered
                            ToolTip.text: (mainModel && mainModel.muted) ? qsTr("Unmute") : qsTr("Mute")

                            onClicked: if (eventBridge) eventBridge.onMuteToggle()
                        }

                        AnimatedImage {
                            anchors.centerIn: parent
                            width: Math.max(Math.min(parent.width, parent.height) * 0.7, 60)
                            height: width
                            source: parent.currentEmotionUrl
                            fillMode: Image.PreserveAspectFit
                            playing: true
                            visible: parent.currentEmotionUrl.length > 0 && parent.currentEmotionUrl.indexOf("file://") === 0
                        }

                        Text {
                            anchors.centerIn: parent
                            text: parent.currentEmotionUrl.indexOf("file://") !== 0 ? (parent.currentEmotionUrl || "😊") : ""
                            font.pixelSize: 80
                            visible: parent.currentEmotionUrl.indexOf("file://") !== 0
                        }
                    }

                    // 对话 + 音乐行
                    Rectangle {
                        id: conversationArea
                        Layout.fillWidth: true
                        property bool showingAttachmentResult: mainModel
                            && (mainModel.ttsText.indexOf("Image analysis:") === 0
                                || mainModel.ttsText.indexOf("Document content:") === 0)
                        Layout.preferredHeight: showingAttachmentResult ? 150 : 72
                        color: "transparent"

                        ScrollView {
                            anchors.fill: parent
                            anchors.margins: Theme.spacingSm
                            clip: true
                            visible: conversationArea.showingAttachmentResult

                            Text {
                                width: parent.width
                                text: (mainModel && mainModel.ttsText) ? mainModel.ttsText : "Idle"
                                font.pixelSize: Theme.fontSizeSm
                                color: Theme.textSecondary
                                verticalAlignment: Text.AlignTop
                                wrapMode: Text.WordWrap
                                textFormat: Text.PlainText
                            }
                        }

                        Column {
                            anchors.fill: parent
                            anchors.margins: Theme.spacingSm
                            spacing: Theme.spacingXs
                            visible: !conversationArea.showingAttachmentResult

                            Text {
                                width: parent.width
                                height: parent.height - (musicLineText.visible ? 22 : 0)
                                text: (mainModel && mainModel.ttsText) ? mainModel.ttsText : "Idle"
                                font.pixelSize: Theme.fontSizeSm
                                color: Theme.textSecondary
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                                wrapMode: Text.WordWrap
                                elide: Text.ElideRight
                            }

                            Text {
                                id: musicLineText
                                width: parent.width
                                visible: mainModel && mainModel.musicLine && mainModel.musicLine.length > 0
                                text: mainModel ? mainModel.musicLine : ""
                                font.pixelSize: Theme.fontSizeXs
                                color: Theme.textPlaceholder
                                horizontalAlignment: Text.AlignHCenter
                                elide: Text.ElideRight
                            }
                        }
                    }
                }
            }

            // 按钮区域
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 112
                    + (root.attachmentName.length > 0 ? 20 : 0)
                    + (root.attachmentStatus.length > 0 ? 18 : 0)
                color: Theme.backgroundSecondary
                visible: !root.isMaximized

                ColumnLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.spacingMd
                    anchors.rightMargin: Theme.spacingMd
                    anchors.topMargin: Theme.spacingSm
                    anchors.bottomMargin: Theme.spacingSm
                    spacing: Theme.spacingXs

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 38
                        spacing: Theme.spacingSm

                        // 手动模式按钮（点击切换录音）
                        XButton {
                            id: manualBtn
                            Layout.preferredWidth: 100
                            Layout.fillWidth: true
                            Layout.maximumWidth: 140
                            Layout.preferredHeight: 38
                            textSize: Theme.fontSizeSm
                            text: (mainModel && mainModel.buttonText) ? mainModel.buttonText : "Hold to Talk"
                            visible: !(mainModel && mainModel.autoMode)

                            onClicked: if (eventBridge) eventBridge.onManualToggle()
                        }

                        // 自动模式按钮
                        XButton {
                            id: autoBtn
                            Layout.preferredWidth: 100
                            Layout.fillWidth: true
                            Layout.maximumWidth: 140
                            Layout.preferredHeight: 38
                            textSize: Theme.fontSizeSm
                            text: (mainModel && mainModel.buttonText) ? mainModel.buttonText : "Start Chat"
                            visible: mainModel && mainModel.autoMode

                            onClicked: if (eventBridge) eventBridge.onAutoStart()
                        }

                        // 打断对话
                        XButton {
                            id: abortBtn
                            Layout.preferredWidth: 80
                            Layout.fillWidth: true
                            Layout.maximumWidth: 120
                            Layout.preferredHeight: 38
                            textSize: Theme.fontSizeSm
                            text: "Interrupt"
                            variant: "secondary"

                            onClicked: if (eventBridge) eventBridge.onAbort()
                        }

                        // 模式切换
                        XButton {
                            id: modeBtn
                            Layout.preferredWidth: 80
                            Layout.fillWidth: true
                            Layout.maximumWidth: 120
                            Layout.preferredHeight: 38
                            textSize: Theme.fontSizeSm
                            text: (mainModel && mainModel.modeText) ? mainModel.modeText : "Manual"
                            variant: "secondary"

                            onClicked: if (eventBridge) eventBridge.onAutoToggle()
                        }

                        // 参数设置
                        XButton {
                            id: settingsBtn
                            Layout.preferredWidth: 80
                            Layout.fillWidth: true
                            Layout.maximumWidth: 120
                            Layout.preferredHeight: 38
                            textSize: Theme.fontSizeSm
                            text: "Settings"
                            variant: "secondary"

                            onClicked: if (eventBridge) eventBridge.onOpenSettings()
                        }
                    }

                    // 输入 + 发送
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.preferredHeight: root.attachmentName.length > 0 ? 60 : 38
                        spacing: Theme.spacingSm

                        Rectangle {
                            id: chatInput
                            Layout.fillWidth: true
                            Layout.preferredHeight: root.attachmentName.length > 0 ? 60 : 38
                            color: Theme.background
                            radius: Theme.radiusMd
                            border.color: textInput.activeFocus ? Theme.primary : Theme.border
                            border.width: 1

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: Theme.spacingXs
                                spacing: 0

                                RowLayout {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 20
                                    spacing: Theme.spacingXs
                                    visible: root.attachmentName.length > 0

                                    Text {
                                        Layout.fillWidth: true
                                        text: root.attachmentName
                                        font.pixelSize: Theme.fontSizeXs
                                        color: Theme.textSecondary
                                        elide: Text.ElideMiddle
                                        verticalAlignment: Text.AlignVCenter
                                    }

                                    ToolButton {
                                        text: "×"
                                        flat: true
                                        Layout.preferredWidth: 24
                                        Layout.preferredHeight: 20
                                        ToolTip.visible: hovered
                                        ToolTip.text: "Remove attachment"
                                        onClicked: clearAttachment()
                                    }
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    spacing: 0

                                    XButton {
                                        implicitWidth: 32
                                        implicitHeight: 32
                                        Layout.preferredWidth: 32
                                        Layout.preferredHeight: 32
                                        topPadding: 0
                                        bottomPadding: 0
                                        leftPadding: 0
                                        rightPadding: 0
                                        ToolTip.visible: hovered
                                        ToolTip.text: "Attach a document or image"
                                        onClicked: attachmentDialog.open()

                                        // "📎" adalah color emoji: mengabaikan color teks,
                                        // jadi clip digambar manual agar bisa berwarna putih.
                                        // Canvas sebagai child biasa (bukan contentItem) supaya
                                        // anchors.centerIn berfungsi normal.
                                        Canvas {
                                            anchors.centerIn: parent
                                            width: 18
                                            height: 18
                                            onPaint: {
                                                var ctx = getContext("2d")
                                                ctx.clearRect(0, 0, width, height)
                                                ctx.strokeStyle = "white"
                                                ctx.lineWidth = 1.6
                                                ctx.lineCap = "round"
                                                ctx.lineJoin = "round"

                                                ctx.save()
                                                // 1. Pindahkan titik pusat (0,0) ke tengah-tengah kanvas (9,9)
                                                ctx.translate(width / 2, height / 2)
                                                
                                                // 2. Putar -45 derajat agar miring persis seperti emoji 📎
                                                ctx.rotate(-Math.PI / 4)
                                                
                                                // 3. Skala sedikit (0.8) agar gambar tidak terpotong di sudut saat berputar
                                                ctx.scale(0.8, 0.8)

                                                ctx.beginPath()
                                                
                                                // --- Alur gambar paperclip double-loop vertikal ---
                                                
                                                // Mulai dari ujung spiral dalam (inner end)
                                                ctx.moveTo(1.5, -2)
                                                ctx.lineTo(1.5, 3)
                                                
                                                // Lengkungan bawah dalam
                                                ctx.arc(0, 3, 1.5, 0, Math.PI, false)
                                                ctx.lineTo(-1.5, -5)
                                                
                                                // Lengkungan atas tengah
                                                ctx.arc(1.5, -5, 3, Math.PI, 2 * Math.PI, false)
                                                ctx.lineTo(4.5, 6)
                                                
                                                // Lengkungan bawah luar (terbesar)
                                                ctx.arc(0, 6, 4.5, 0, Math.PI, false)
                                                ctx.lineTo(-4.5, -3)

                                                ctx.stroke()
                                                ctx.restore()
                                            }
                                        }
                                    }

                                    TextInput {
                                        id: textInput
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        Layout.leftMargin: 6
                                        verticalAlignment: TextInput.AlignVCenter
                                        font.pixelSize: Theme.fontSizeSm
                                        color: Theme.textPrimary
                                        selectByMouse: true
                                        clip: true
                                        leftPadding: 4
                                        rightPadding: 4

                                        Keys.priority: Keys.BeforeItem
                                        Keys.onPressed: function(event) {
                                            var pasteModifier = Qt.ControlModifier | Qt.MetaModifier
                                            if (event.key === Qt.Key_V && (event.modifiers & pasteModifier)) {
                                                var pasted = eventBridge ? eventBridge.onPasteClipboard() : ({})
                                                if (pasted && pasted.path) {
                                                    root.attachmentPath = pasted.path
                                                    root.attachmentName = pasted.name
                                                    root.attachmentUseDocument = !!pasted.useDocumentTool
                                                    root.attachmentStatus = "Clipboard attached temporarily"
                                                    event.accepted = true
                                                }
                                            }
                                        }

                                        Text {
                                            anchors.fill: parent
                                            text: "Type a message..."
                                            font: textInput.font
                                            color: Theme.textPlaceholder
                                            verticalAlignment: Text.AlignVCenter
                                            visible: !textInput.text && !textInput.activeFocus
                                        }

                                        Keys.onReturnPressed: sendText()
                                    }
                                }
                            }
                        }

                        XButton {
                            id: sendBtn
                            Layout.preferredWidth: 80
                            Layout.preferredHeight: 38
                            Layout.alignment: Qt.AlignVCenter
                            textSize: Theme.fontSizeSm
                            text: "Send"

                            onClicked: sendText()
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 18
                        visible: root.attachmentStatus.length > 0
                        text: root.attachmentStatus
                        font.pixelSize: Theme.fontSizeXs
                        color: root.attachmentStatus.indexOf("Could not") === 0
                            || root.attachmentStatus.indexOf("Unsupported") === 0
                            || root.attachmentStatus.indexOf("No readable") === 0
                            ? Theme.error : Theme.textSecondary
                        elide: Text.ElideRight
                        verticalAlignment: Text.AlignVCenter
                    }

                    // Counter karakter untuk input pesan; berlaku baik saat attach file
                    // maupun saat mengetik pesan biasa. Channel "detect" server menolak
                    // teks >= 32 char, jadi tampilkan "n/32" di seluruh mode input.
                    Text {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 16
                        text: {
                            let n = textInput.text.trim().length
                            return n + "/32"
                        }
                        font.pixelSize: Theme.fontSizeXs
                        color: textInput.text.trim().length >= 32
                            ? Theme.error : Theme.textPlaceholder
                        horizontalAlignment: Text.AlignRight
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }
        }

    function sendText() {
        let text = textInput.text.trim()
        if (attachmentPath.length > 0 && eventBridge) {
            attachmentStatus = "Analyzing attachment..."
            attachmentStatusTimer.stop()
            eventBridge.onSendAttachment(
                attachmentPath, text, attachmentUseDocument
            )
            clearAttachment()
            textInput.text = ""
        } else if (text.length > 0 && eventBridge) {
            eventBridge.onSendText(text)
            textInput.text = ""
        }
    }

    function clearAttachment() {
        attachmentPath = ""
        attachmentName = ""
        attachmentUseDocument = false
    }
}
