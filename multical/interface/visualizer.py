from collections import OrderedDict
import math

from .viewer_3d.viewer_3d import Viewer3D
from .viewer_3d.moving_board import MovingBoard
# from .viewer_3d.moving_cameras import MovingCameras

from . import camera_params, view_table
from .layout import h_layout, v_layout, widget
import PyQt5.QtWidgets as QtWidgets

from PyQt5 import uic, QtCore
from PyQt5.QtCore import Qt

from multical import image, tables

from .viewer_image import ViewerImage, annotate_image
from structs.struct import struct, split_dict

import qtawesome as qta


def visualize(workspace):
  app = QtWidgets.QApplication([])

  vis = Visualizer()

  vis.update_workspace(workspace)
  vis.showMaximized()

  app.exec_()


class Updating(object):
  def __init__(self, widget):
    self.widget = widget

  def __enter__(self):
    self.was_ready = self.widget.ready

  def __exit__(self, type, value, traceback):
    self.widget.ready = self.was_ready


def if_ready(func):
  def f(self, *args, **kwargs):
    if self.ready:
      return func(self, *args, **kwargs)
  return f

def void(func):
  def f(self, *args, **kwargs):
    return func(self)
  return f


class Visualizer(QtWidgets.QMainWindow):
  def __init__(self):
    super(Visualizer, self).__init__()
    uic.loadUi('multical/interface/visualizer.ui', self)

    self.setFocusPolicy(Qt.StrongFocus)
    # self.grabKeyboard()

    self.viewer_3d = Viewer3D(self)
    self.viewer_image = ViewerImage(self)

    self.view_frame.setLayout(h_layout(self.viewer_3d, margin=0))
    self.image_frame.setLayout(h_layout(self.viewer_image, margin=0))

    self.controller = None
    self.controllers = None

    self.splitter.setStretchFactor(0, 2)
    self.splitter.setStretchFactor(1, 1)

    self.params_viewer = camera_params.ParamsViewer(self)
    self.cameras_tab.setLayout(h_layout(self.params_viewer))
    self.ready = False

    action = QtWidgets.QAction(qta.icon('fa.folder-open'), "new", self)
    self.toolBar.addAction(action)

    action = QtWidgets.QAction(qta.icon('fa.save'), "Save", self)
    self.toolBar.addAction(action)

    action = QtWidgets.QAction(qta.icon('fa.cogs'), "Optimize", self)
    self.toolBar.addAction(action)

    self.toolBar.addSeparator()

    self.calibrations_combo = QtWidgets.QComboBox(self)

    toolbar_combo = widget(h_layout(QtWidgets.QLabel("Stage"), self.calibrations_combo), self)
    self.toolBar.addWidget(toolbar_combo)

    self.setDisabled(True)

  def update_calibrations(self, calibrations):
    with(self.updating):
      has_calibrations = len(calibrations) > 0

      self.tab_3d.setEnabled(has_calibrations)
      self.cameras_tab.setEnabled(has_calibrations)

      calib_names, calibs = split_dict(calibrations)

      self.calibrations_combo.clear()
      self.calibrations_combo.addItems(calib_names)
      self.calibrations_combo.setCurrentIndex(len(calibs) - 1)

      if has_calibrations:
        self.set_calibration(len(calib_names) - 1)
      else:
        self.setup_view_table(None)

      _, layer_labels = self.image_layers()
      self.layer_combo.clear()
      self.layer_combo.addItems(layer_labels)
    self.update_controller()


  def set_calibration(self, index):
    with(self.updating):
      ws = self.workspace

      assert index < len(ws.calibrations)
      _, calibs = split_dict(ws.calibrations)

      self.calibration = calibs[index]
      self.params_viewer.set_cameras(self.calibration.cameras, tables.inverse(
          self.calibration.pose_estimates.camera))

      self.viewer_3d.enable(False)
      scale=self.camera_size()

      if self.controllers is None:
        self.controllers = struct(
          # moving_cameras=MovingCameras(self.viewer_3d),
          moving_board=MovingBoard(self.viewer_3d, self.calibration, ws.board_colors, scale)
        )
      else:
        for controller in self.controllers.values():
          controller.set_calibration(self.calibration)

      self.setup_view_table(self.calibration)
      
      self.viewer_3d.enable(True)
      self.viewer_3d.fix_camera()
      
    self.update_controller()

  def setup_view_table(self, calibration):
    
    with(self.updating):

      ws = self.workspace
      board_labels = ["All boards"] + \
          [f"Board: {name}" for name in self.workspace.names.board]

      self.boards_combo.clear()
      self.boards_combo.addItems(board_labels)

      self.view_model = view_table.ViewModelDetections(ws.point_table, ws.names)\
          if calibration is None else view_table.ViewModelCalibrated(calibration, ws.names)

      self.view_table.setModel(self.view_model)
      self.view_table.setSelectionMode(
          QtWidgets.QAbstractItemView.SingleSelection)

      self.metric_combo.clear()
      self.metric_combo.addItems(self.view_model.metric_labels)

    self.select(0, 0)


  @property
  def updating(self):
    return Updating(self)

  def update_workspace(self, workspace):
    with(self.updating):
      self.workspace = workspace

      for record in workspace.log_entries:
        self.log_viewer.insertPlainText(record.message + "\n")

      self.params_viewer.init(self.workspace.names.camera)

      self.calibrations = self.workspace.get_calibrations()
      self.update_calibrations(self.calibrations)

      self.setDisabled(False)
      self.connect_ui()

    self.ready = True
    self.update_frame()
    self.update_controller()


  def keyPressEvent(self, event):

    if event.key() == Qt.Key.Key_Up:
      self.move_frame(-1)
    elif event.key() == Qt.Key.Key_Down:
      self.move_frame(1)
    elif event.key() == Qt.Key.Key_Left:
      self.move_camera(-1)
    elif event.key() == Qt.Key.Key_Right:
      self.move_camera(1)
    self.update()

  def camera_size(self):
    return self.camera_size_slider.value() / 500.0

  def select(self, frame, camera):
    index = self.view_model.index(frame, camera)
    self.view_table.selectionModel().select(
        index, QtCore.QItemSelectionModel.ClearAndSelect)

  def selection(self):
    selection = self.view_table.selectionModel().selectedIndexes()
    assert len(selection) == 1

    return selection[0].row(), selection[0].column()

  def state(self):
    frame, camera = self.selection()
    names = self.workspace.names
    images = self.workspace.images

    return struct(
        frame=frame,
        camera=camera,
        scale=self.camera_size(),
        camera_name=names.camera[camera],
        image_name=names.image[frame],
        image=images[camera][frame]
    )

  def move_frame(self, d_frame=0):
    sizes = self.workspace.sizes

    frame, camera = self.selection()
    self.select((frame + d_frame) % sizes.image, camera)

  def move_camera(self, d_camera=0):
    sizes = self.workspace.sizes

    frame, camera = self.selection()
    self.select(frame, (camera + d_camera) % sizes.camera)

  @if_ready
  @void
  def update_frame(self):
    state = self.state()
    if self.controller is not None:
      self.controller.update(state)

    self.statusBar().showMessage(f"{state.camera_name} {state.image_name}")
    self.update_image()

  def image_layers(self):
    layers = OrderedDict()
    if self.calibration is not None:
      layers['reprojection'] = "Reprojection"
    layers['detections'] = "Detections"

    if self.workspace.pose_table is not None:
      layers['detected_poses'] = "Detected poses"

    return split_dict(layers)

  @if_ready
  @void
  def update_image(self):
    state = self.state()
    layer_names, _ = self.image_layers()

    layer_name = layer_names[self.layer_combo.currentIndex()]

    options = struct(
        marker_size=self.marker_size_slider.value(),
        line_width=self.line_width_slider.value(),
        show_ids=self.show_ids_check.isChecked()
    )

    annotated_image = annotate_image(self.workspace, self.calibration,
                                     layer_name, state, options=options)

    self.viewer_image.update_image(annotated_image)

  @void
  @if_ready
  def update_controller(self):
    if self.controller is not None:
      self.controller.disable()

    # if self.moving_cameras_check.isChecked():
    #   self.controller = self.controllers.moving_cameras
    # else:
    #   self.controller = self.controllers.moving_board

    self.controller = self.controllers.moving_board


    self.controller.enable(self.state())

  @void
  @if_ready
  def update_view_table(self):
    metric_selected = self.metric_combo.currentIndex()
    board_index = self.boards_combo.currentIndex()    
    board = None if board_index <= 0 else board_index - 1

    self.view_model.set_metric(metric_selected, board)

  def connect_ui(self):
    self.camera_size_slider.valueChanged.connect(self.update_frame)
    self.view_table.selectionModel().selectionChanged.connect(self.update_frame)
    self.metric_combo.currentIndexChanged.connect(self.update_view_table)
    self.boards_combo.currentIndexChanged.connect(self.update_view_table)
    self.calibrations_combo.currentIndexChanged.connect(self.set_calibration)

    for layer_check in [self.show_ids_check]:
      layer_check.toggled.connect(self.update_image)
      
    self.layer_combo.currentIndexChanged.connect(self.update_image)
    self.marker_size_slider.valueChanged.connect(self.update_image)
    self.line_width_slider.valueChanged.connect(self.update_image)

    self.line_size_slider.valueChanged.connect(self.viewer_3d.set_line_size)
    self.moving_cameras_check.toggled.connect(self.update_controller)
