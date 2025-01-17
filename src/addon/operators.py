
import bpy
import os

from . import importer

"""
Imports a map file into blender
"""
class MapImporter(bpy.types.Operator):
    bl_idname = 'cod_asset_importer.map_importer'
    bl_label = 'Import'
    bl_options = {'UNDO'}

    filepath : bpy.props.StringProperty(subtype='FILE_PATH')
    filename_ext = '.d3dbsp'
    filter_glob : bpy.props.StringProperty(default='*.d3dbsp;*.bsp', options={'HIDDEN'})

    def execute(self, context):
        assetpath = os.path.abspath(os.path.join(os.path.dirname(self.filepath), os.pardir))
        if os.path.basename(self.filepath).startswith("mp_"):
            assetpath = os.path.abspath(os.path.join(os.path.dirname(self.filepath), os.pardir, os.pardir))

        importer.import_ibsp(assetpath, self.filepath)
        return {'FINISHED'}

    def invoke(self, context, event):
        bpy.context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

"""
Imports xmodel file into blender
"""
class XModelImporter(bpy.types.Operator):
    bl_idname = 'cod_asset_importer.xmodel_importer'
    bl_label = 'Import'
    bl_options = {'UNDO'}

    filepath : bpy.props.StringProperty(subtype='FILE_PATH')
    def execute(self, context):
        assetpath = os.path.abspath(os.path.join(os.path.dirname(self.filepath), os.pardir))
        importer.import_xmodel(assetpath, self.filepath, True)
        return {'FINISHED'}

    def invoke(self, context, event):
        bpy.context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}