// 应用主入口
import QtQuick
import QtQuick.Window

import "windows"

// 主窗口作为根元素；初始隐藏，由 QmlAppHost.show_root 决定是否抢前台
MainWindow {
    id: mainWindow
    visible: false

    // 设置窗口 - 使用 Loader 延迟加载（作为独立窗口）
    Loader {
        id: settingsLoader
        active: false
        source: "windows/SettingsWindow.qml"

        function showWindow() {
            if (status !== Loader.Ready || !item)
                return
            item.visible = true
            item.raise()
            item.requestActivate()
        }

        onLoaded: {
            showWindow()
        }

        onStatusChanged: {
            if (status === Loader.Error)
                console.error("Settings window failed to load:", errorString())
        }
    }

    // 监听 eventBridge 的信号来控制设置窗口
    Connections {
        target: eventBridge

        function onShowSettingsWindow() {
            if (settingsLoader.status === Loader.Ready) {
                settingsLoader.showWindow()
            } else if (settingsLoader.status !== Loader.Error) {
                settingsLoader.active = true
            } else {
                console.error("Settings window is unavailable:", settingsLoader.errorString())
            }
        }
    }
}
