# Copyright (c) 2013 Shotgun Software Inc.
#
# CONFIDENTIAL AND PROPRIETARY
#
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights
# not expressly granted therein are reserved by Shotgun Software Inc.

"""
A Houdini engine for Tank.
"""

import os
import sys
import ctypes
import shutil

import tank

import hou


class HoudiniEngine(tank.platform.Engine):
    def init_engine(self):
        self.log_debug("%s: Initializing..." % self)

        if hou.applicationVersion()[0] < 12:
            raise tank.TankError("Your version of Houdini is not supported. Currently, Toolkit only supports version 12+")

        # add our built-in pyside to the python path when on windows
        if sys.platform == "win32":
            pyside_path = os.path.join(self.disk_location, "resources", "pyside112_py26_win64")
            sys.path.append(pyside_path)

        self.__created_qt_dialogs = []

    def post_app_init(self):
        tk_houdini = self.import_module("tk_houdini")
        bootstrap = tk_houdini.bootstrap

        if bootstrap.g_temp_env in os.environ:
            menu_file = os.path.join(os.environ[bootstrap.g_temp_env], 'MainMenuCommon')

            # as of houdini 12.5 add .xml
            if hou.applicationVersion() > (12, 5, 0):
                menu_file = menu_file + ".xml"

            # Figure out the tmp OP Library path for this session
            oplibrary_path = os.environ[bootstrap.g_temp_env].replace("\\", "/")

        menu = tk_houdini.MenuGenerator(self)
        if not os.path.exists(menu_file):
            # just create the xml for the menus
            menu.create_menu(menu_file)

        # get map of id to callback
        self._callback_map = menu.callback_map()

        # Setup the OTLs that need to be loaded for the Toolkit apps
        self._load_otls(oplibrary_path)

        # startup Qt
        from tank.platform.qt import QtGui
        from tank.platform.qt import QtCore

        app = QtGui.QApplication.instance()
        if app is None:
            # create the QApplication
            sys.argv[0] = 'Shotgun'
            app = QtGui.QApplication(sys.argv)
            QtGui.QApplication.setStyle("cleanlooks")
            app.setQuitOnLastWindowClosed(False)
            app.setApplicationName(sys.argv[0])

            # tell QT to interpret C strings as utf-8
            utf8 = QtCore.QTextCodec.codecForName("utf-8")
            QtCore.QTextCodec.setCodecForCStrings(utf8)

            # set the stylesheet
            app.setStyleSheet(self._get_standard_qt_stylesheet())

        tk_houdini.python_qt_houdini.exec_(app)

    def destroy_engine(self):
        self.log_debug("%s: Destroying..." % self)

        tk_houdini = self.import_module("tk_houdini")
        bootstrap = tk_houdini.bootstrap
        if bootstrap.g_temp_env in os.environ:
            # clean up and keep on going
            shutil.rmtree(os.environ[bootstrap.g_temp_env])

    def _load_otls(self, oplibrary_path):
        """
        Load any OTLs provided by applications.

        Look in any application folder for a otls subdirectory and load any .otl
        file from there.
        """
        for app in self.apps.values():
            otl_path = os.path.join(app.disk_location, 'otls')
            if not os.path.exists(otl_path):
                continue

            for filename in os.listdir(otl_path):
                if os.path.splitext(filename)[-1] == '.otl':
                    path = os.path.join(otl_path, filename).replace("\\", "/")
                    hou.hda.installFile(path, oplibrary_path, True)

    # qt support
    ############################################################################
    def _define_qt_base(self):
        """
        check for pyside then pyqt
        """
        # proxy class used when QT does not exist on the system.
        # this will raise an exception when any QT code tries to use it
        class QTProxy(object):
            def __getattr__(self, name):
                raise tank.TankError("Looks like you are trying to run an App that uses a QT "
                                     "based UI, however the Houdini engine could not find a PyQt "
                                     "or PySide installation in your python system path. We "
                                     "recommend that you install PySide if you want to "
                                     "run UI applications from Houdini.")

        base = {"qt_core": QTProxy(), "qt_gui": QTProxy(), "dialog_base": None}
        self._ui_type = None

        if not self._ui_type:
            try:
                from PySide import QtCore, QtGui
                import PySide

                base["qt_core"] = QtCore
                base["qt_gui"] = QtGui
                base["dialog_base"] = QtGui.QDialog
                self.log_debug("Successfully initialized PySide %s located "
                    "in %s." % (PySide.__version__, PySide.__file__))
                self._ui_type = "PySide"
            except ImportError:
                pass
            except Exception, e:
                import traceback
                self.log_warning("Error setting up pyside. Pyside based UI "
                    "support will not be available: %s" % e)
                self.log_debug(traceback.format_exc())

        if not self._ui_type:
            try:
                from PyQt4 import QtCore, QtGui
                import PyQt4

                # hot patch the library to make it work with pyside code
                QtCore.Signal = QtCore.pyqtSignal
                QtCore.Slot = QtCore.pyqtSlot
                QtCore.Property = QtCore.pyqtProperty
                base["qt_core"] = QtCore
                base["qt_gui"] = QtGui
                base["dialog_base"] = QtGui.QDialog
                self.log_debug("Successfully initialized PyQt %s located "
                    "in %s." % (QtCore.PYQT_VERSION_STR, PyQt4.__file__))
                self._ui_type = "PyQt"
            except ImportError:
                pass
            except Exception, e:
                import traceback
                self.log_warning("Error setting up PyQt. PyQt based UI support "
                    "will not be available: %s" % e)
                self.log_debug(traceback.format_exc())

        return base

    def _create_dialog(self, title, bundle, obj):
        from tank.platform.qt import tankqdialog

        dialog = tankqdialog.TankQDialog(title, bundle, obj, None)
        dialog.raise_()
        dialog.activateWindow()

        # get windows to raise the dialog
        if sys.platform == "win32":
            ctypes.pythonapi.PyCObject_AsVoidPtr.restype = ctypes.c_void_p
            ctypes.pythonapi.PyCObject_AsVoidPtr.argtypes = [ctypes.py_object]
            if self._ui_type == "PySide":
                hwnd = ctypes.pythonapi.PyCObject_AsVoidPtr(dialog.winId())
            elif self._ui_type == "PyQt":
                hwnd = ctypes.pythonapi.PyCObject_AsVoidPtr(dialog.winId().ascobject())
            else:
                raise NotImplementedError("Unsupported ui type: %s" % self._ui_type)
            ctypes.windll.user32.SetActiveWindow(hwnd)

        return dialog

    def show_modal(self, title, bundle, widget_class, *args, **kwargs):
        if not self._ui_type:
            self.log_error("Cannot show dialog %s! No QT support appears to exist in this engine. "
                           "In order for the houdini engine to run UI based apps, either pyside "
                           "or PyQt needs to be installed in your system." % title)
            return

        obj = widget_class(*args, **kwargs)
        dialog = self._create_dialog(title, bundle, obj)
        status = dialog.exec_()
        return status, obj

    def show_dialog(self, title, bundle, widget_class, *args, **kwargs):
        if not self._ui_type:
            self.log_error("Cannot show dialog %s! No QT support appears to exist in this engine. "
                           "In order for the houdini engine to run UI based apps, either pyside "
                           "or PyQt needs to be installed in your system." % title)
            return

        obj = widget_class(*args, **kwargs)
        dialog = self._create_dialog(title, bundle, obj)
        self.__created_qt_dialogs.append(dialog)
        dialog.show()
        return obj

    def _display_message(self, msg):
        if hou.isUIAvailable():
            hou.ui.displayMessage(str(msg))
        else:
            print str(msg)

    def launch_command(self, cmd_id):
        callback = self._callback_map.get(cmd_id)
        if callback is None:
            self.log_error("No callback found for id: %s" % cmd_id)
            return
        callback()

    def log_debug(self, msg):
        if self.get_setting("debug_logging", False):
            print "Shotgun Debug: %s" % msg

    def log_info(self, msg):
        print "Shotgun: %s" % msg

    def log_error(self, msg):
        self._display_message(msg)
        print "Shotgun Error: %s" % msg

    def log_warning(self, msg):
        print str(msg)
