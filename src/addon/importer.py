from __future__ import annotations

import glob
import os
import bpy
import bmesh
import mathutils
import math
import numpy
import time
import subprocess
import traceback

from .. assets import (
    ibsp as ibsp_asset,
    material as material_asset,
    texture as texture_asset,
    xmodel as xmodel_asset,
    xmodelpart as xmodelpart_asset,
    xmodelsurf as xmodelsurf_asset
)

from .. utils import (
    blender as blenderutils,
    log
)

# ----------------------------------------------------------------------------------
# MAP IMPORT -----------------------------------------------------------------------
# ----------------------------------------------------------------------------------

"""
Import an IBSP file
"""
def import_ibsp(assetpath: str, filepath: str) -> bool:
    start_time_ibsp = time.monotonic()

    IBSP = ibsp_asset.IBSP()
    if not IBSP.load(filepath):
        log.error_log(f"Error loading map: {filepath}")
        return False
    
    map_null = bpy.data.objects.new(IBSP.name, None)
    bpy.context.scene.collection.objects.link(map_null)

    map_geometry_null = bpy.data.objects.new(f"{IBSP.name}_geometry", None)
    bpy.context.scene.collection.objects.link(map_geometry_null)
    map_geometry_null.parent = map_null

    map_entities_null = bpy.data.objects.new(f"{IBSP.name}_entities", None)
    bpy.context.scene.collection.objects.link(map_entities_null)
    map_entities_null.parent = map_null

    # import materials
    for material in IBSP.materials:
        if IBSP.version == ibsp_asset.VERSIONS.COD1:
            material_name = os.path.join(*material.name.split('/')) # material names are path names as well, so we create a proper path

            # the extension is not defined inside the v59 format 
            # so we try to match a pattern and retrieve the first matching file 
            texture_file = ''
            for tex in glob.iglob(os.path.join(assetpath, material_name + '.*')):
                texture_file = tex.removeprefix(assetpath).lstrip('/\\')
                break
            
            if texture_file == '':
                continue

            _import_material_v14(assetpath, texture_file)
        else:
            _import_material_v20(assetpath, material.name)

    # import surfaces
    for surface in IBSP.surfaces:
        name = f"{IBSP.name}_geometry"

        mesh = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, mesh)
        obj.parent = map_geometry_null

        if IBSP.version == ibsp_asset.VERSIONS.COD1:
            obj.active_material = bpy.data.materials.get(os.path.join(*surface.material.split('/')))
        else:
            obj.active_material = bpy.data.materials.get(surface.material)

        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)

        mesh_data = bpy.context.object.data
        bm = bmesh.new()

        surface_uvs = []
        surface_vertex_colors = []
        surface_normals = []

        for triangle in surface.triangles:
            
            vertex1 = surface.vertices[triangle[0]]
            vertex2 = surface.vertices[triangle[2]]
            vertex3 = surface.vertices[triangle[1]]

            v1 = bm.verts.new(vertex1.position.to_tuple())
            v2 = bm.verts.new(vertex2.position.to_tuple())
            v3 = bm.verts.new(vertex3.position.to_tuple())

            triangle_uvs = []
            triangle_uvs.append(vertex1.uv.to_tuple())
            triangle_uvs.append(vertex2.uv.to_tuple())
            triangle_uvs.append(vertex3.uv.to_tuple())
            surface_uvs.append(triangle_uvs)

            triangle_vertex_colors = []
            triangle_vertex_colors.append(vertex1.color.to_tuple())
            triangle_vertex_colors.append(vertex2.color.to_tuple())
            triangle_vertex_colors.append(vertex3.color.to_tuple())
            surface_vertex_colors.append(triangle_vertex_colors)

            triangle_normals = []
            triangle_normals.append(vertex1.normal.to_tuple())
            triangle_normals.append(vertex2.normal.to_tuple())
            triangle_normals.append(vertex3.normal.to_tuple())
            surface_normals.append(triangle_normals)

            bm.verts.ensure_lookup_table()
            bm.verts.index_update()

            bm.faces.new((v1, v2, v3))
            bm.faces.ensure_lookup_table()
            bm.faces.index_update()

        uv_layer = bm.loops.layers.uv.new()
        vertex_color_layer = bm.loops.layers.color.new()
        vertex_normal_buffer = []

        for face, uv, color, normal in zip(bm.faces, surface_uvs, surface_vertex_colors, surface_normals):
            for loop, uv_data, color_data, normal_data in zip(face.loops, uv, color, normal):
                loop[uv_layer].uv = uv_data
                loop[vertex_color_layer] = color_data
                vertex_normal_buffer.append(normal_data)

        bm.to_mesh(mesh_data)
        bm.free()

        # set normals        
        mesh.create_normals_split()
        mesh.validate(clean_customdata=False)
        mesh.normals_split_custom_set(vertex_normal_buffer)

        polygon_count = len(mesh.polygons)
        mesh.polygons.foreach_set('use_smooth', [True] * polygon_count)
        mesh.use_auto_smooth = True

    # entities
    unique_entities = {}
    for entity in IBSP.entities:
        if entity.name in unique_entities:
            entity_null = blenderutils.copy_object_hierarchy(unique_entities[entity.name])[0]
            bpy.ops.object.select_all(action='DESELECT')
        else:
            entity_path = os.path.join(assetpath, xmodel_asset.XModel.PATH, entity.name)
            entity_null = import_xmodel(assetpath, entity_path, True)
            
        if entity_null:
            entity_null.parent = map_entities_null
            entity_null.location = entity.origin.to_tuple()
            entity_null.scale = entity.scale.to_tuple()
            entity_null.rotation_euler = (
                math.radians(entity.angles.z), 
                math.radians(entity.angles.x), 
                math.radians(entity.angles.y)
            )

            if entity.name not in unique_entities:
                unique_entities[entity.name] = entity_null

    done_time_d3dbsp = time.monotonic()
    log.info_log(f"Imported map: {IBSP.name} [{round(done_time_d3dbsp - start_time_ibsp, 2)}s]")

    return True

# ----------------------------------------------------------------------------------
# MODEL IMPORT ---------------------------------------------------------------------
# ----------------------------------------------------------------------------------

"""
Import an xmodel file
"""
def import_xmodel(assetpath: str, filepath: str, import_skeleton: bool) -> bpy.types.Object | bool:
    start_time_xmodel = time.monotonic()

    XMODEL = xmodel_asset.XModel()
    if not XMODEL.load(filepath):
        log.error_log(f"Error loading xmodel: {filepath}")
        return False

    lod0 = XMODEL.lods[0]

    XMODELPART = xmodelpart_asset.XModelPart()
    xmodel_part = os.path.join(assetpath, xmodelpart_asset.XModelPart.PATH, lod0.name)
    if not XMODELPART.load(xmodel_part):
        log.error_log(f"Error loading xmodelpart: {xmodel_part}")
        XMODELPART = None

    XMODELSURF = xmodelsurf_asset.XModelSurf()
    xmodel_surf = os.path.join(assetpath, xmodelsurf_asset.XModelSurf.PATH, lod0.name)
    if not XMODELSURF.load(xmodel_surf, XMODELPART):
        log.error_log(f"Error loading xmodelsurf: {xmodel_surf}")
        return False

    xmodel_null = bpy.data.objects.new(XMODEL.name, None)
    bpy.context.scene.collection.objects.link(xmodel_null)

    mesh_objects = []

    # import materials
    for material in lod0.materials:
        if XMODEL.version == xmodel_asset.VERSIONS.COD1:
            _import_material_v14(os.path.join(assetpath, 'skins'), material)
        elif XMODEL.version == xmodel_asset.VERSIONS.COD2:
            _import_material_v20(assetpath, material)
        elif XMODEL.version == xmodel_asset.VERSIONS.COD4:
            _import_material_v25(assetpath, material)

    # create mesh
    for i, surface in enumerate(XMODELSURF.surfaces):
        mesh = bpy.data.meshes.new(XMODELSURF.name)
        obj = bpy.data.objects.new(XMODELSURF.name, mesh)

        if XMODEL.version == xmodel_asset.VERSIONS.COD1:
            obj.active_material = bpy.data.materials.get(os.path.splitext(lod0.materials[i])[0])
        else:
            obj.active_material = bpy.data.materials.get(lod0.materials[i])
            

        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)


        mesh_data = bpy.context.object.data
        bm = bmesh.new()
        vertex_weight_layer = bm.verts.layers.deform.new()

        surface_uvs = []
        surface_vertex_colors = []
        surface_normals = []

        for triangle in surface.triangles:
            
            vertex1 = surface.vertices[triangle[0]]
            vertex2 = surface.vertices[triangle[2]]
            vertex3 = surface.vertices[triangle[1]]

            triangle_uvs = []
            triangle_uvs.append(vertex1.uv.to_tuple())
            triangle_uvs.append(vertex2.uv.to_tuple())
            triangle_uvs.append(vertex3.uv.to_tuple())
            surface_uvs.append(triangle_uvs)

            triangle_vertex_colors = []
            triangle_vertex_colors.append(vertex1.color.to_tuple())
            triangle_vertex_colors.append(vertex2.color.to_tuple())
            triangle_vertex_colors.append(vertex3.color.to_tuple())
            surface_vertex_colors.append(triangle_vertex_colors)

            triangle_normals = []
            triangle_normals.append(vertex1.normal.to_tuple())
            triangle_normals.append(vertex2.normal.to_tuple())
            triangle_normals.append(vertex3.normal.to_tuple())
            surface_normals.append(triangle_normals)

            v1 = bm.verts.new(vertex1.position.to_tuple())
            v2 = bm.verts.new(vertex2.position.to_tuple())
            v3 = bm.verts.new(vertex3.position.to_tuple())

            bm.verts.ensure_lookup_table()
            bm.verts.index_update()

            verts_assoc = {
                v1: vertex1,
                v2: vertex2,
                v3: vertex3
            }

            for bvert, svert in verts_assoc.items():
                for weight in svert.weights:
                    bm.verts[bvert.index][vertex_weight_layer][weight.bone] = weight.influence

            bm.faces.new((v1, v2, v3))
            bm.faces.ensure_lookup_table()
            bm.faces.index_update()

        uv_layer = bm.loops.layers.uv.new()
        vertex_color_layer = bm.loops.layers.color.new()
        vertex_normal_buffer = []

        for face, uv, color, normal in zip(bm.faces, surface_uvs, surface_vertex_colors, surface_normals):
            for loop, uv_data, color_data, normal_data in zip(face.loops, uv, color, normal):
                loop[uv_layer].uv = uv_data
                loop[vertex_color_layer] = color_data
                vertex_normal_buffer.append(normal_data)

        bm.to_mesh(mesh_data)
        bm.free()

        # set normals        
        mesh.create_normals_split()
        mesh.validate(clean_customdata=False)
        mesh.normals_split_custom_set(vertex_normal_buffer)

        polygon_count = len(mesh.polygons)
        mesh.polygons.foreach_set('use_smooth', [True] * polygon_count)
        mesh.use_auto_smooth = True

        mesh_objects.append(obj)

    # create skeleton
    skeleton = None
    if import_skeleton and XMODELPART != None and len(XMODELPART.bones) > 1:

        armature = bpy.data.armatures.new(f"{lod0.name}_armature")
        armature.display_type = 'STICK'

        skeleton = bpy.data.objects.new(f"{lod0.name}_skeleton", armature)
        skeleton.parent = xmodel_null
        skeleton.show_in_front = True
        bpy.context.scene.collection.objects.link(skeleton)
        bpy.context.view_layer.objects.active = skeleton
        bpy.ops.object.mode_set(mode='EDIT')

        bone_matrices = {}

        for bone in XMODELPART.bones:

            new_bone = armature.edit_bones.new(bone.name)
            new_bone.tail = (0, 0.05, 0)

            matrix_rotation = bone.local_transform.rotation.to_matrix().to_4x4()
            matrix_transform = mathutils.Matrix.Translation(bone.local_transform.position)

            matrix = matrix_transform @ matrix_rotation
            bone_matrices[bone.name] = matrix

            if bone.parent > -1:
                new_bone.parent = armature.edit_bones[bone.parent]

        bpy.context.view_layer.objects.active = skeleton
        bpy.ops.object.mode_set(mode='POSE')

        for bone in skeleton.pose.bones:
            bone.matrix_basis.identity()
            bone.matrix = bone_matrices[bone.name]
        
        bpy.ops.pose.armature_apply()
        bpy.context.view_layer.objects.active = skeleton

        maxs = [0,0,0]
        mins = [0,0,0]

        for bone in armature.bones:
            for i in range(3):
                maxs[i] = max(maxs[i], bone.head_local[i])
                mins[i] = min(mins[i], bone.head_local[i])

        dimensions = []
        for i in range(3):
            dimensions.append(maxs[i] - mins[i])

        length = max(0.001, (dimensions[0] + dimensions[1] + dimensions[2]) / 600)
        bpy.ops.object.mode_set(mode='EDIT')
        for bone in [armature.edit_bones[b.name] for b in XMODELPART.bones]:
            bone.tail = bone.head + (bone.tail - bone.head).normalized() * length

        bpy.ops.object.mode_set(mode='OBJECT')

    for mesh_object in mesh_objects:
        if skeleton == None:
            mesh_object.parent = xmodel_null
            continue

        for bone in XMODELPART.bones:
            mesh_object.vertex_groups.new(name=bone.name)

        mesh_object.parent = skeleton
        modifier = mesh_object.modifiers.new('armature_rig', 'ARMATURE')
        modifier.object = skeleton
        modifier.use_bone_envelopes = False
        modifier.use_vertex_groups = True

    bpy.context.view_layer.update()
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')

    done_time_xmodel = time.monotonic()
    log.info_log(f"Imported xmodel: {lod0.name} [{round(done_time_xmodel - start_time_xmodel, 2)}s]")

    return xmodel_null

# ----------------------------------------------------------------------------------
# MATERIAL IMPORT ------------------------------------------------------------------
# ----------------------------------------------------------------------------------

"""
Import a material file for CoD1 & CoD:UO (v14) assets and create node setup
"""
def _import_material_v14(assetpath: str, material_name: str) -> bool:
    start_time_material = time.monotonic()

    texture_file = os.path.join(assetpath, material_name)
    material_name = os.path.splitext(material_name)[0] # strip off the extension when creating the name

    if bpy.data.materials.get(material_name):
        return True
    
    try:
        texture_image = bpy.data.images.load(texture_file, check_existing=True)
        material = bpy.data.materials.new(material_name)
        material.use_nodes = True
        material.blend_method = 'HASHED'
        material.shadow_method = 'HASHED'

        nodes = material.node_tree.nodes
        links = material.node_tree.links

        output_node = None
        for node in nodes:
            if node.type != 'OUTPUT_MATERIAL':
                nodes.remove(node)
                continue

            if node.type == 'OUTPUT_MATERIAL' and output_node == None:
                output_node = node

        if output_node == None:
            output_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_OUTPUTMATERIAL)

        output_node.location = (300, 0)

        mix_shader_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_MIXSHADER)
        mix_shader_node.location = (100, 0)
        links.new(mix_shader_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_MIXSHADER_SHADER], output_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_OUTPUTMATERIAL_SURFACE])

        transparent_bsdf_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_BSDFTRANSPARENT)
        transparent_bsdf_node.location = (-200, 100)
        links.new(transparent_bsdf_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_BSDFTRANSPARENT_BSDF], mix_shader_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MIXSHADER_SHADER1])

        principled_bsdf_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_BSDFPRINCIPLED)
        principled_bsdf_node.location = (-200, 0)
        principled_bsdf_node.width = 200
        links.new(principled_bsdf_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_BSDFTRANSPARENT_BSDF], mix_shader_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MIXSHADER_SHADER2])

        texture_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_TEXIMAGE)
        texture_node.label = 'colorMap'
        texture_node.location = (-700, 0)
        texture_node.image = texture_image
        links.new(texture_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_TEXIMAGE_COLOR], principled_bsdf_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_BSDFPRINCIPLED_BASECOLOR])

        invert_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_INVERT)
        invert_node.location = (-400, 0)
        
        invert_fac_default_value = 0.0
        transparent_textures = [
            'foliage_masked',
            'foliage_detail'
        ]
        for tt in transparent_textures:
            if tt in material_name.lower():
                invert_fac_default_value = 1.0
                break
        
        invert_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_INVERT_FAC].default_value = invert_fac_default_value

        links.new(invert_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_INVERT_COLOR], mix_shader_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MIXSHADER_FAC])
        links.new(texture_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_TEXIMAGE_ALPHA], invert_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_INVERT_COLOR])

        done_time_material = time.monotonic()
        log.info_log(f"Imported material: {material_name} [{round(done_time_material - start_time_material, 2)}s]")
        return True

    except:
        log.error_log(traceback.print_exc())
        return False


"""
Import a material file for CoD2 (v20) assets and create node setup
"""
def _import_material_v20(assetpath: str, material_name: str) -> bool:
    if bpy.data.materials.get(material_name):
        return True

    start_time_material = time.monotonic()

    MATERIAL = material_asset.Material()
    material_file = os.path.join(assetpath, material_asset.Material.PATH, material_name)
    if not MATERIAL.load(xmodel_asset.VERSIONS.COD2, material_file):
        log.error_log(f"Error loading material: {material_name}")
        return False
    
    material = bpy.data.materials.new(MATERIAL.name)
    material.use_nodes = True
    material.blend_method = 'HASHED'
    material.shadow_method = 'HASHED'

    nodes = material.node_tree.nodes
    links = material.node_tree.links

    output_node = None
    for node in nodes:
        if node.type != 'OUTPUT_MATERIAL':
            nodes.remove(node)
            continue

        if node.type == 'OUTPUT_MATERIAL' and output_node == None:
            output_node = node

    if output_node == None:
        output_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_OUTPUTMATERIAL)

    output_node.location = (300, 0)

    mix_shader_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_MIXSHADER)
    mix_shader_node.location = (100, 0)
    links.new(mix_shader_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_MIXSHADER_SHADER], output_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_OUTPUTMATERIAL_SURFACE])

    transparent_bsdf_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_BSDFTRANSPARENT)
    transparent_bsdf_node.location = (-200, 100)
    links.new(transparent_bsdf_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_BSDFTRANSPARENT_BSDF], mix_shader_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MIXSHADER_SHADER1])

    principled_bsdf_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_BSDFPRINCIPLED)
    principled_bsdf_node.location = (-200, 0)
    principled_bsdf_node.width = 200
    links.new(principled_bsdf_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_BSDFTRANSPARENT_BSDF], mix_shader_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MIXSHADER_SHADER2])

    for i, t in enumerate(MATERIAL.textures):
        texture_image = _import_texture(assetpath, t.name)
        if texture_image == False:
            continue

        texture_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_TEXIMAGE)
        texture_node.label = t.type
        texture_node.location = (-700, -255 * i)
        texture_node.image = texture_image

        if t.type == texture_asset.TEXTURE_TYPE.COLORMAP:
            links.new(texture_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_TEXIMAGE_COLOR], principled_bsdf_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_BSDFPRINCIPLED_BASECOLOR])
            links.new(texture_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_TEXIMAGE_ALPHA], mix_shader_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MIXSHADER_FAC])
        elif t.type == texture_asset.TEXTURE_TYPE.SPECULARMAP:
            links.new(texture_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_TEXIMAGE_COLOR], principled_bsdf_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_BSDFPRINCIPLED_SPECULAR])
            texture_node.image.colorspace_settings.name = blenderutils.BLENDER_SHADERNODES.TEXIMAGE_COLORSPACE_LINEAR
            texture_node.location = (-700, -255)
        elif t.type == texture_asset.TEXTURE_TYPE.NORMALMAP:
            texture_node.image.colorspace_settings.name = blenderutils.BLENDER_SHADERNODES.TEXIMAGE_COLORSPACE_LINEAR
            texture_node.location = (-1900, -655)
            
            normal_map_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_NORMALMAP)
            normal_map_node.location = (-450, -650)
            normal_map_node.space = blenderutils.BLENDER_SHADERNODES.NORMALMAP_SPACE_TANGENT
            normal_map_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_NORMALMAP_STRENGTH].default_value = 0.3
            links.new(normal_map_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_NORMALMAP_NORMAL], principled_bsdf_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_BSDFPRINCIPLED_NORMAL])

            combine_rgb_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_COMBINERGB)
            combine_rgb_node.location = (-650, -750)
            links.new(combine_rgb_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_COMBINERGB_IMAGE], normal_map_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_NORMALMAP_COLOR])

            math_sqrt_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_MATH)
            math_sqrt_node.location = (-850, -850)
            math_sqrt_node.operation = blenderutils.BLENDER_SHADERNODES.OPERATION_MATH_SQRT
            links.new(math_sqrt_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_MATH_VALUE], combine_rgb_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_COMBINERGB_B])

            math_subtract_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_MATH)
            math_subtract_node.location = (-1050, -850)
            math_subtract_node.operation = blenderutils.BLENDER_SHADERNODES.OPERATION_MATH_SUBTRACT
            links.new(math_subtract_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_MATH_VALUE], math_sqrt_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_SQRT_VALUE])

            math_subtract_node2 = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_MATH)
            math_subtract_node2.location = (-1250, -950)
            math_subtract_node2.operation = blenderutils.BLENDER_SHADERNODES.OPERATION_MATH_SUBTRACT
            math_subtract_node2.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_SUBTRACT_VALUE1].default_value = 1.0
            links.new(math_subtract_node2.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_MATH_VALUE], math_subtract_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_SUBTRACT_VALUE1])

            math_power_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_MATH)
            math_power_node.location = (-1250, -750)
            math_power_node.operation = blenderutils.BLENDER_SHADERNODES.OPERATION_MATH_POWER
            math_power_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_POWER_EXPONENT].default_value = 2.0
            links.new(math_power_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_MATH_VALUE], math_subtract_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_SUBTRACT_VALUE2])

            math_power_node2 = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_MATH)
            math_power_node2.location = (-1500, -950)
            math_power_node2.operation = blenderutils.BLENDER_SHADERNODES.OPERATION_MATH_POWER
            math_power_node2.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_POWER_EXPONENT].default_value = 2.0
            links.new(math_power_node2.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_MATH_VALUE], math_subtract_node2.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_SUBTRACT_VALUE2])
            links.new(texture_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_TEXIMAGE_ALPHA], math_power_node2.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_POWER_BASE])

            separate_rgb_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_SEPARATERGB)
            separate_rgb_node.location = (-1500, -450)
            links.new(separate_rgb_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_SEPARATERGB_G], combine_rgb_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_COMBINERGB_G])
            links.new(separate_rgb_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_SEPARATERGB_G], math_power_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_POWER_BASE])
            links.new(texture_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_TEXIMAGE_COLOR], separate_rgb_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_SEPARATERGB_IMAGE])
            links.new(texture_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_TEXIMAGE_ALPHA], math_power_node2.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_POWER_BASE])
            links.new(texture_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_TEXIMAGE_ALPHA], combine_rgb_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_COMBINERGB_R])

    done_time_material = time.monotonic()
    log.info_log(f"Imported material: {MATERIAL.name} [{round(done_time_material - start_time_material, 2)}s]")

    return True

"""
Import a material file for CoD4 (v26) assets and create node setup
"""
def _import_material_v25(assetpath: str, material_name: str) -> bool:
    if bpy.data.materials.get(material_name):
        return True

    start_time_material = time.monotonic()

    MATERIAL = material_asset.Material()
    material_file = os.path.join(assetpath, material_asset.Material.PATH, material_name)
    if not MATERIAL.load(xmodel_asset.VERSIONS.COD4, material_file):
        log.error_log(f"Error loading material: {material_name}")
        return False
    
    material = bpy.data.materials.new(MATERIAL.name)
    material.use_nodes = True
    material.blend_method = 'HASHED'
    material.shadow_method = 'HASHED'

    nodes = material.node_tree.nodes
    links = material.node_tree.links

    output_node = None
    for node in nodes:
        if node.type != 'OUTPUT_MATERIAL':
            nodes.remove(node)
            continue

        if node.type == 'OUTPUT_MATERIAL' and output_node == None:
            output_node = node

    if output_node == None:
        output_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_OUTPUTMATERIAL)

    output_node.location = (300, 0)

    mix_shader_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_MIXSHADER)
    mix_shader_node.location = (100, 0)
    links.new(mix_shader_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_MIXSHADER_SHADER], output_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_OUTPUTMATERIAL_SURFACE])

    transparent_bsdf_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_BSDFTRANSPARENT)
    transparent_bsdf_node.location = (-200, 100)
    links.new(transparent_bsdf_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_BSDFTRANSPARENT_BSDF], mix_shader_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MIXSHADER_SHADER1])

    principled_bsdf_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_BSDFPRINCIPLED)
    principled_bsdf_node.location = (-200, 0)
    principled_bsdf_node.width = 200
    links.new(principled_bsdf_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_BSDFTRANSPARENT_BSDF], mix_shader_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MIXSHADER_SHADER2])

    for i, t in enumerate(MATERIAL.textures):
        texture_image = _import_texture(assetpath, t.name)
        if texture_image == False:
            continue

        texture_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_TEXIMAGE)
        texture_node.label = t.type
        texture_node.location = (-700, -255 * i)
        texture_node.image = texture_image

        if t.type == texture_asset.TEXTURE_TYPE.COLORMAP:
            links.new(texture_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_TEXIMAGE_COLOR], principled_bsdf_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_BSDFPRINCIPLED_BASECOLOR])
            links.new(texture_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_TEXIMAGE_ALPHA], mix_shader_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MIXSHADER_FAC])
        elif t.type == texture_asset.TEXTURE_TYPE.SPECULARMAP:
            links.new(texture_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_TEXIMAGE_COLOR], principled_bsdf_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_BSDFPRINCIPLED_SPECULAR])
            texture_node.image.colorspace_settings.name = blenderutils.BLENDER_SHADERNODES.TEXIMAGE_COLORSPACE_LINEAR
            texture_node.location = (-700, -255)
        elif t.type == texture_asset.TEXTURE_TYPE.NORMALMAP:
            texture_node.image.colorspace_settings.name = blenderutils.BLENDER_SHADERNODES.TEXIMAGE_COLORSPACE_LINEAR
            texture_node.location = (-1900, -655)
            
            normal_map_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_NORMALMAP)
            normal_map_node.location = (-450, -650)
            normal_map_node.space = blenderutils.BLENDER_SHADERNODES.NORMALMAP_SPACE_TANGENT
            normal_map_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_NORMALMAP_STRENGTH].default_value = 0.3
            links.new(normal_map_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_NORMALMAP_NORMAL], principled_bsdf_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_BSDFPRINCIPLED_NORMAL])

            combine_rgb_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_COMBINERGB)
            combine_rgb_node.location = (-650, -750)
            links.new(combine_rgb_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_COMBINERGB_IMAGE], normal_map_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_NORMALMAP_COLOR])

            math_sqrt_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_MATH)
            math_sqrt_node.location = (-850, -850)
            math_sqrt_node.operation = blenderutils.BLENDER_SHADERNODES.OPERATION_MATH_SQRT
            links.new(math_sqrt_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_MATH_VALUE], combine_rgb_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_COMBINERGB_B])

            math_subtract_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_MATH)
            math_subtract_node.location = (-1050, -850)
            math_subtract_node.operation = blenderutils.BLENDER_SHADERNODES.OPERATION_MATH_SUBTRACT
            links.new(math_subtract_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_MATH_VALUE], math_sqrt_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_SQRT_VALUE])

            math_subtract_node2 = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_MATH)
            math_subtract_node2.location = (-1250, -950)
            math_subtract_node2.operation = blenderutils.BLENDER_SHADERNODES.OPERATION_MATH_SUBTRACT
            math_subtract_node2.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_SUBTRACT_VALUE1].default_value = 1.0
            links.new(math_subtract_node2.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_MATH_VALUE], math_subtract_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_SUBTRACT_VALUE1])

            math_power_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_MATH)
            math_power_node.location = (-1250, -750)
            math_power_node.operation = blenderutils.BLENDER_SHADERNODES.OPERATION_MATH_POWER
            math_power_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_POWER_EXPONENT].default_value = 2.0
            links.new(math_power_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_MATH_VALUE], math_subtract_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_SUBTRACT_VALUE2])

            math_power_node2 = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_MATH)
            math_power_node2.location = (-1500, -950)
            math_power_node2.operation = blenderutils.BLENDER_SHADERNODES.OPERATION_MATH_POWER
            math_power_node2.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_POWER_EXPONENT].default_value = 2.0
            links.new(math_power_node2.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_MATH_VALUE], math_subtract_node2.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_SUBTRACT_VALUE2])
            links.new(texture_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_TEXIMAGE_ALPHA], math_power_node2.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_POWER_BASE])

            separate_rgb_node = nodes.new(blenderutils.BLENDER_SHADERNODES.SHADERNODE_SEPARATERGB)
            separate_rgb_node.location = (-1500, -450)
            links.new(separate_rgb_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_SEPARATERGB_G], combine_rgb_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_COMBINERGB_G])
            links.new(separate_rgb_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_SEPARATERGB_G], math_power_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_POWER_BASE])
            links.new(texture_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_TEXIMAGE_COLOR], separate_rgb_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_SEPARATERGB_IMAGE])
            links.new(texture_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_TEXIMAGE_ALPHA], math_power_node2.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_MATH_POWER_BASE])
            links.new(texture_node.outputs[blenderutils.BLENDER_SHADERNODES.OUTPUT_TEXIMAGE_ALPHA], combine_rgb_node.inputs[blenderutils.BLENDER_SHADERNODES.INPUT_COMBINERGB_R])

    done_time_material = time.monotonic()
    log.info_log(f"Imported material: {MATERIAL.name} [{round(done_time_material - start_time_material, 2)}s]")

    return True

# ----------------------------------------------------------------------------------
# TEXTURE IMPORT -------------------------------------------------------------------
# ----------------------------------------------------------------------------------

"""
Import an IWi texture file
"""
def _import_texture(assetpath: str, texture_name: str) -> bpy.types.Texture | bool:
    start_time_texture = time.monotonic()
    
    texture_image = bpy.data.images.get(texture_name)
    if texture_image != None:
        return texture_image

    texture_file = os.path.join(assetpath, texture_asset.IWi.PATH, texture_name)

    # if no .dds exists then try to convert it on the fly via iwi2dds 
    if not os.path.isfile(texture_file + '.dds'):
        iwi2dds = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, 'bin', 'iwi2dds.exe'))
        if os.path.isfile(iwi2dds):
            try:
                result = subprocess.run((iwi2dds, '-i', texture_file + '.iwi'), capture_output=True)
                if result.returncode != 0:
                    log.error_log(result.stderr.decode('utf-8'))

            except:
                log.error_log(traceback.print_exc())

    try:
        # try to load .dds 
        texture_image = bpy.data.images.load(texture_file + '.dds', check_existing=True)

    except:

        # if error happens when converting or loading the dds just fall back to .iwi parsing 
        TEXTURE = texture_asset.IWi()
        if not TEXTURE.load(texture_file + '.iwi'):
            log.error_log(f"Error loading texture: {texture_name}")
            return False

        texture_image = bpy.data.images.new(texture_name, TEXTURE.width, TEXTURE.height, alpha=True)
        pixels = TEXTURE.texture_data

        # flip the image
        p = numpy.asarray(pixels)
        p.shape = (TEXTURE.height, TEXTURE.width, 4)
        p = numpy.flipud(p)
        texture_image.pixels = p.flatten().tolist()

    texture_image.file_format = 'TARGA'
    texture_image.pack()

    done_time_texture = time.monotonic()
    log.info_log(f"Imported texture: {texture_name} [{round(done_time_texture - start_time_texture, 2)}s]")

    return texture_image
