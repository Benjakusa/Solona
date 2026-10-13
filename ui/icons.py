"""Consistent, emoji-free icons built from the active Qt style."""
from PySide6.QtWidgets import QStyle, QWidget


_STANDARD_PIXMAPS = {
    "add": QStyle.SP_FileDialogNewFolder,
    "edit": QStyle.SP_FileDialogInfoView,
    "delete": QStyle.SP_TrashIcon,
    "search": QStyle.SP_FileDialogContentsView,
    "refresh": QStyle.SP_BrowserReload,
    "save": QStyle.SP_DialogSaveButton,
    "cancel": QStyle.SP_DialogCancelButton,
    "accept": QStyle.SP_DialogApplyButton,
    "warning": QStyle.SP_MessageBoxWarning,
    "info": QStyle.SP_MessageBoxInformation,
    "print": QStyle.SP_DialogSaveButton,
    "folder": QStyle.SP_DirOpenIcon,
    "file": QStyle.SP_FileIcon,
    "lock": QStyle.SP_MessageBoxWarning,
    "home": QStyle.SP_DirHomeIcon,
    "users": QStyle.SP_FileDialogListView,
    "settings": QStyle.SP_FileDialogInfoView,
    "logout": QStyle.SP_DialogCloseButton,
    "cart": QStyle.SP_FileDialogListView,
    "history": QStyle.SP_BrowserBack,
}


def icon(widget: QWidget, name: str):
    """Return a native Qt icon, falling back to a neutral file icon."""
    pixmap = _STANDARD_PIXMAPS.get(name, QStyle.SP_FileIcon)
    return widget.style().standardIcon(pixmap)
