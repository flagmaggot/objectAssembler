from itertools import chain, combinations

import bpy, bgl
from bpy_extras import view3d_utils
from mathutils import Vector

ALLOWED_NAVIGATION = {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE',
                      'NUMPAD_1', 'NUMPAD_3', 'NUMPAD_7', 'NUMPAD_5' ,
                      'NUMPAD_2', 'NUMPAD_4', 'NUMPAD_6', 'NUMPAD_8',
                      'NUMPAD_PERIOD', "Z"}

MAX_ERROR_DIST = 1e-6
MAX_ERROR_EQL = 2e-4
MAX_ERROR_FINE = 1e-9

def select_and_active(obj):
    # deselect everything
    bpy.ops.object.select_all(action='DESELECT')

    # select object, set object active
    obj.select = True
    bpy.context.scene.objects.active = obj


def toggle():
    bpy.ops.object.editmode_toggle()


def double_toggle():
    toggle()
    toggle()


def get_cursor_info(context):
    return context.space_data.pivot_point, context.scene.cursor_location.copy()

def set_cursor_info(context, cursor_info):
    context.space_data.pivot_point = cursor_info[0]
    context.scene.cursor_location = cursor_info[1]


def point_in_polygon(x,y,poly):
    n = len(poly)
    inside = False

    p1x,p1y = poly[0]
    for i in range(n+1):
        p2x,p2y = poly[i % n]
        if y > min(p1y,p2y):
            if y <= max(p1y,p2y):
                if x <= max(p1x,p2x):
                    if p1y != p2y:
                        xinters = (y-p1y)*(p2x-p1x)/(p2y-p1y)+p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x,p1y = p2x,p2y

    return inside


def ray(self, context, obj_name_black_list = [], ray_max=10000.0):
    scene = context.scene
    region = context.region
    rv3d = context.region_data # <==> rv3d = context.space_data.region_3d
    coord = self.mouse # event.mouse_region_x, event.mouse_region_y

    if (rv3d.view_perspective == 'ORTHO') or (rv3d.view_perspective == 'CAMERA' and scene.camera.data.type == 'ORTHO'):
        view_vector = -1 * view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord) + view_vector * - ray_max / 2
    else:
        view_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)

    ray_target = ray_origin + (view_vector * ray_max)


    def visible_objects_and_duplis():
        for obj in context.visible_objects:
            if (obj.type == 'MESH') and (obj.name not in obj_name_black_list) and (("OASnapPointsParameters" not in obj) and ("marked" not in obj.OASnapPointsParameters)):
                yield (obj, obj.matrix_world.copy(), obj)

            if obj.dupli_type == 'GROUP':
                obj.dupli_list_create(scene)
                for dob in obj.dupli_list:
                    obj_dupli = dob.object
                    if obj_dupli.type == 'MESH' and \
                            obj.name not in obj_name_black_list and \
                            obj_dupli.name != obj.dupli_group.name:
                        yield (obj_dupli, dob.matrix.copy(), obj)

            obj.dupli_list_clear()


    def obj_ray_cast(obj, matrix):

        # get the ray relative to the object
        matrix_inv = matrix.inverted()
        ray_origin_obj = matrix_inv * ray_origin
        ray_target_obj = matrix_inv * ray_target

        # cast the ray
        hit, normal, face_index = obj.ray_cast(ray_origin_obj, ray_target_obj)

        if face_index != -1:
            return hit, normal, face_index
        else:
            return None, None, None
        
    # cast rays and find the closest object
    best_length_squared = ray_max * ray_max
    best_obj = None
    
    for obj, matrix, group_or_obj in visible_objects_and_duplis():
        if obj.type == 'MESH':
            hit, normal, face_index = obj_ray_cast(obj, matrix)
            if hit is not None:
                length_squared = (matrix * hit - ray_origin).length_squared
                if length_squared < best_length_squared:
                    best_length_squared = length_squared
                    best_obj = group_or_obj
    
    return best_obj

def get_tool_shelf_width(context):
    tool_shelf_width = 0
    if bpy.context.user_preferences.system.use_region_overlap:
        for region in bpy.context.area.regions:
            if region.type == 'TOOLS':
                tool_shelf_width = region.width
    
    return tool_shelf_width if tool_shelf_width else 0

def get_center_from_geometry(obj):
    vector_sum = Vector((0,0,0))
    vector_count = 0
    for v in obj.data.vertices:
        vector_sum += obj.matrix_world * v.co
        vector_count += 1
    return vector_sum / vector_count

def move_origin_to_geometry(obj):
    if not len(obj.data.vertices): return
    old_origin = obj.location.copy()
    new_origin = get_center_from_geometry(obj)
    obj.location = new_origin
    offset = old_origin - new_origin
    for v in obj.data.vertices: v.co += offset

def get_oa_group(obj):
    ''' get excatly one object assembler group or return None '''
    if not obj: return None
    oa_group = None
    for group in obj.users_group:
        if group.OAGroup.oa_type != 'NONE':
            if oa_group is not None:
                return None
            oa_group = group
            
    return oa_group

def get_sp_obj(obj):
    ''' get excatly one snap point object or return None '''
    # doesn't check for multiple sp_objs - error checking-ops should do that
    if not obj: return None
    sp_obj = None
    for group in obj.users_group:
        for obj_in_group in group.objects:
            if obj_in_group.type == 'MESH' and obj_in_group.OASnapPoints.marked:
                return obj_in_group
    return sp_obj

def get_sp_obj_from_base_id(base):
    for group in bpy.data.groups:
        if group.OAGroup.oa_type == 'BASE':
            if tuple(group.OAGroup.oa_id) == base:
                for obj in group.objects:
                    if obj.type == 'MESH' and obj.OASnapPoints.marked:
                        return obj

def convert_base_id_to_array(group):
    params = group.OAGroup
    # base_id: from '(0, 1, 2)' to ['0', '1', '2']
    base_id = params.base_id.replace('(','').replace(')','').replace(' ','').split(',')
    # base_id: convert to (0,1,2)
    base_id = tuple(map(int, base_id))
    return base_id

def powerset(iterable):
    "powerset([1,2,3]) --> () (1,) (2,) (3,) (1,2) (1,3) (2,3) (1,2,3)"
    s = list(iterable)
    return chain.from_iterable(combinations(s, r) for r in range(len(s)+1))

def powerset_without_empty_set(iterable):
    "powerset([1,2,3]) --> (1,) (2,) (3,) (1,2) (1,3) (2,3) (1,2,3)"
    s = list(iterable)
    return chain.from_iterable(combinations(s, r) for r in range(1, len(s)+1))