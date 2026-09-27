// 按钮组件
import QtQuick
import QtQuick.Controls
import "../theme"

Button {
    id: root

    property string variant: "primary"  // "primary", "secondary", "danger", "text"
    // 紧凑场景（如表格内的操作按钮）可以覆盖字号
    property int textSize: Theme.fontSizeMd

    implicitWidth: Math.max(80, contentItem.implicitWidth + Theme.spacingLg * 2)
    implicitHeight: 36

    background: Rectangle {
        radius: Theme.radiusSm
        color: {
            if (!root.enabled) return Theme.backgroundSecondary
            if (root.variant === "text") return root.pressed ? Theme.backgroundPressed : (root.hovered ? Theme.backgroundHover : "transparent")
            if (root.variant === "danger") return root.pressed ? Theme.errorBorder : (root.hovered ? Theme.errorLight : "transparent")
            if (root.variant === "secondary") return root.pressed ? Theme.backgroundPressed : (root.hovered ? Theme.backgroundHover : Theme.backgroundSecondary)
            return root.pressed ? Theme.primaryPressed : (root.hovered ? Theme.primaryHover : Theme.primary)
        }
        border.width: (root.variant === "secondary" || root.variant === "danger") ? 1 : 0
        border.color: root.variant === "danger" ? Theme.error : Theme.border

        Behavior on color {
            ColorAnimation { duration: Theme.animationFast }
        }
    }

    contentItem: Text {
        text: root.text
        font.family: Theme.fontFamily
        font.pixelSize: root.textSize
        color: {
            if (!root.enabled) return Theme.textPlaceholder
            if (root.variant === "danger") return Theme.error
            if (root.variant === "primary") return "white"
            return Theme.textPrimary
        }
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
}
