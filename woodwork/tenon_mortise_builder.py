import bmesh
import bpy
from mathutils.geometry import intersect_point_line
from math import pi
from sys import float_info

def nearly_equal(a, b, epsilon=0.00001):
    abs_a = abs(a)
    abs_b = abs(b)
    diff = abs(a - b)

    if a == b:
        return True
    elif a == 0.0 or b == 0.0 or diff < float_info.min:
        return diff < (epsilon * float_info.min)
    else:
        return diff / (abs_a + abs_b) < epsilon


def same_direction(tangent0, tangent1):
    angle = tangent0.angle(tangent1)

    return nearly_equal(angle, 0.0) or nearly_equal(angle, pi)


def distance_point_edge(pt, edge):
    line_p1 = edge.verts[0].co
    line_p2 = edge.verts[1].co
    ret = intersect_point_line(pt, line_p1, line_p2)
    closest_point_on_line = ret[0]
    distance_vector = closest_point_on_line - pt
    return distance_vector.length


def vector_abs(vector):
    for i in range(len(vector)):
        if vector[i] < 0.0:
            vector[i] = abs(vector[i])


def constraint_axis_from_tangent(tangent):
    if tangent[0] == -1.0 or tangent[0] == 1:
        return True, False, False
    elif tangent[1] == -1.0 or tangent[1] == 1:
        return False, True, False
    return False, False, True

class TenonMortiseBuilderThickness:
    pass


class TenonMortiseBuilderHeight:
    pass


class TenonMortiseBuilderProps:
    def __init__(self):
        self.height_properties = TenonMortiseBuilderHeight()
        self.thickness_properties = TenonMortiseBuilderThickness()

# This describes the initial face where the tenon will be created
class FaceToBeTransformed:
    def __init__(self, face):
        self.face = face

        self.median = None
        self.longest_side_tangent = None
        self.shortest_side_tangent = None
        self.longest_edges = None
        self.shortest_edges = None
        self.shortest_length = None
        self.longest_length = None

    def extract_features(self, matrix_world):
        face = self.face

        # Get center
        self.median = face.calc_center_median()

        # Get largest and smallest edge to find resize axes
        l0 = face.loops[0]
        e0 = l0.edge
        l1 = face.loops[1]
        e1 = l1.edge

        v0 = matrix_world * e0.verts[0].co
        v1 = matrix_world * e0.verts[1].co
        length0 = (v0 - v1).length
        v0 = matrix_world * e1.verts[0].co
        v1 = matrix_world * e1.verts[1].co
        length1 = (v0 - v1).length

        if length0 > length1:
            self.longest_side_tangent = e0.calc_tangent(l0)
            self.shortest_side_tangent = e1.calc_tangent(l1)
            self.longest_edges = [e0, face.loops[2].edge]
            self.shortest_edges = [e1, face.loops[3].edge]
            self.shortest_length = length1
            self.longest_length = length0
        else:
            self.longest_side_tangent = e1.calc_tangent(l1)
            self.shortest_side_tangent = e0.calc_tangent(l0)
            self.longest_edges = [e1, face.loops[3].edge]
            self.shortest_edges = [e0, face.loops[2].edge]
            self.shortest_length = length0
            self.longest_length = length1

    # Subdivide given edges and return created faces
    def __subdivide_edges(self, bm, edges_to_subdivide):
        ret = bmesh.ops.subdivide_edges(
            bm,
            edges=edges_to_subdivide,
            cuts=2,
            use_grid_fill=True)

        # Get the new faces

        # Can't rely on Faces as certain faces are not tagged when only two
        # edges are subdivided
        # see  source / blender / bmesh / operators / bmo_subdivide.c
        new_edges = [bmesh_type
                     for bmesh_type in ret["geom_inner"]
                     if type(bmesh_type) is bmesh.types.BMEdge]
        del ret
        subdivided_faces = set()
        for new_edge in new_edges:
            for linked_face in new_edge.link_faces:
                subdivided_faces.add(linked_face)
        return subdivided_faces

    # Subdivide face to be transformed to a tenon
    def subdivide_face(self, bm, height_properties, thickness_properties):
        edges_to_subdivide = []

        max_centered_height = bool(height_properties.type == "max" and
                                   height_properties.centered)
        max_centered_thickness = bool(thickness_properties.type == "max" and
                                      thickness_properties.centered)

        if max_centered_height and not max_centered_thickness:
            # if tenon height set to maximum, select shortest side edges
            # to subdivide only in this direction
            for edge in self.shortest_edges:
                edges_to_subdivide.append(edge)

        elif max_centered_thickness and not max_centered_height:
            # if tenon thickness set to maximum, select longest side edges
            # to subdivide only in this direction
            for edge in self.longest_edges:
                edges_to_subdivide.append(edge)

        elif not (max_centered_height and max_centered_thickness):
            edges_to_subdivide = self.face.edges

        return self.__subdivide_edges(bm, edges_to_subdivide)


# This structure keep info about the newly created tenon face
class TenonFace:
    def __init__(self, face):
        self.face = face
        self.thickness_faces = []
        self.height_faces = []
        self.thickness_reference_edge = None
        self.height_reference_edge = None

    # find tenon adjacent faces to be translated or resized given user's values
    # - height_faces[] are the faces which follows the direction of the
    # longest side
    # - thickness_faces[] are the faces which follows the direction of the
    # shortest side
    # - thickness_reference_edge and height_reference_edge are tenon edges used
    # to determine scale factor
    def find_adjacent_faces(self,
                            face_to_be_transformed,
                            height_properties,
                            thickness_properties):
        tenon = self.face

        self.thickness_faces.append(tenon)
        self.height_faces.append(tenon)

        longest_side_tangent = face_to_be_transformed.longest_side_tangent
        shortest_side_tangent = face_to_be_transformed.shortest_side_tangent

        # Find faces to resize to obtain tenon base
        tenon_edges = tenon.edges
        for tenon_edge in tenon_edges:
            connected_faces = tenon_edge.link_faces
            for connected_face in connected_faces:
                if connected_face != tenon:
                    # Found face adjacent to tenon
                    connected_loops = tenon_edge.link_loops
                    for connected_loop in connected_loops:
                        if connected_loop.face == connected_face:
                            # Return the tangent at this edge relative to
                            # a face (pointing inward into the face).
                            tangent = tenon_edge.calc_tangent(connected_loop)

                            if same_direction(
                                tangent,
                                longest_side_tangent):

                                self.height_faces.append(connected_face)

                                if self.height_reference_edge is None:
                                    self.height_reference_edge = tenon_edge
                            else:
                                self.thickness_faces.append(connected_face)

                                if self.thickness_reference_edge is None:
                                    self.thickness_reference_edge = tenon_edge

        if height_properties.type == "max" and height_properties.centered:
            # get tenon side facing the smallest side
            l0 = tenon.loops[0]
            e0 = l0.edge
            l1 = tenon.loops[1]
            e1 = l1.edge

            tangent0 = e0.calc_tangent(l0)

            if same_direction(tangent0,
                              shortest_side_tangent):
                self.thickness_reference_edge = e0
            else:
                self.thickness_reference_edge = e1

        elif thickness_properties.type == "max" and \
                thickness_properties.centered:
            # get tenon side facing the longest side
            l0 = tenon.loops[0]
            e0 = l0.edge
            l1 = tenon.loops[1]
            e1 = l1.edge

            tangent0 = e0.calc_tangent(l0)

            if same_direction(tangent0, longest_side_tangent):
                self.height_reference_edge = e0
            else:
                self.height_reference_edge = e1

    def get_scale_factor(self, reference_edge, matrix_world, resize_value):
        v0 = reference_edge.verts[0].co
        v1 = reference_edge.verts[1].co

        v0_world = matrix_world * v0
        v1_world = matrix_world * v1

        to_be_resized = (v0_world - v1_world).length

        return resize_value / to_be_resized

    def compute_translation_vector_given_shoulder(self,
                                                  reference_edge,
                                                  shoulder,
                                                  scale_factor,
                                                  matrix_world):

        v0 = reference_edge.verts[0].co
        v1 = reference_edge.verts[1].co

        v0_world = matrix_world * v0
        v1_world = matrix_world * v1

        length1 = distance_point_edge(v1, shoulder.origin_face_edge)
        length0 = distance_point_edge(v0, shoulder.origin_face_edge)

        if length1 > length0:
            edge_vector = v1_world - v0_world
        else:
            edge_vector = v0_world - v1_world
        final_vector = edge_vector * scale_factor

        return final_vector - edge_vector

    def find_verts_to_translate(self, tenon_faces, shoulder_verts):
        tenon_verts = set()
        for face in tenon_faces:
            verts = face.verts
            for vert in verts:
                tenon_verts.add(vert)

        return tenon_verts.difference(shoulder_verts)


# This describes a shoulder adjacent to the tenon face
class ShoulderFace:
    def __init__(self):
        self.face = None
        self.reference_edge = None
        self.origin_face_edge = None

    # gets the shoulder : it's a face in tenon_adjacent_faces that is not
    # the tenon itself
    def get_from_tenon(self,
                       tenon,
                       tenon_adjacent_faces,
                       reverse_shoulder,
                       origin_face_edges):

        for face in tenon_adjacent_faces:
            if face != tenon.face:
                if reverse_shoulder:
                    if self.face is not None:
                        self.face = face
                        # TODO : take the edge that match shoulder face
                        self.origin_face_edge = origin_face_edges[1]
                        break
                    else:
                        self.face = face
                else:
                    self.face = face
                    # TODO : take the edge that match shoulder face
                    self.origin_face_edge = origin_face_edges[0]
                    break

    def find_verts_to_translate(self, origin_face_tangent, tenon_faces):

        # find faces to scale
        shoulder_face = self.face
        shoulder_faces = [shoulder_face]

        for edge in shoulder_face.edges:
            connected_faces = edge.link_faces

            for connected_face in connected_faces:
                if connected_face != shoulder_face:
                    connected_loops = edge.link_loops

                    for connected_loop in connected_loops:
                        if connected_loop.face == shoulder_face:
                            tangent = edge.calc_tangent(connected_loop)

                            if same_direction(tangent, origin_face_tangent):
                                shoulder_faces.append(connected_face)

                                if self.reference_edge is None:
                                    self.reference_edge = edge

        # when height or thickness set to the max and tenon is centered,
        # this could happen...
        if self.reference_edge is None:
            l0 = shoulder_face.loops[0]
            e0 = l0.edge
            l1 = shoulder_face.loops[1]
            e1 = l1.edge

            tangent0 = e0.calc_tangent(l0)

            if same_direction(tangent0, origin_face_tangent):
                self.reference_edge = e0
            else:
                self.reference_edge = e1

        # find vertices to move
        shoulder_verts = set()
        for face in shoulder_faces:
            verts = face.verts
            for vert in verts:
                shoulder_verts.add(vert)
        tenon_verts = set()
        for face in tenon_faces:
            verts = face.verts
            for vert in verts:
                tenon_verts.add(vert)
        return shoulder_verts.intersection(tenon_verts)

    def compute_translation_vector(self, shoulder_value, matrix_world):
        # compute scale factor
        pt1 = self.reference_edge.verts[1].co
        pt0 = self.reference_edge.verts[0].co

        length1 = distance_point_edge(pt1, self.origin_face_edge)
        length0 = distance_point_edge(pt0, self.origin_face_edge)
        if length1 > length0:
            edge_vector = (matrix_world * pt1) - (matrix_world * pt0)
        else:
            edge_vector = (matrix_world * pt0) - (matrix_world * pt1)
        shoulder_length_to_resize = edge_vector.length
        scale_factor = shoulder_value / shoulder_length_to_resize
        final_vector = edge_vector * scale_factor
        return final_vector - edge_vector


class TenonMortiseBuilder:
    def __init__(self, face_to_be_transformed, builder_properties):
        self.face_to_be_transformed = face_to_be_transformed
        self.builder_properties = builder_properties

    # Extrude and fatten to set face length
    def __set_face_depth(self, depth, bm, matrix_world, face):

        ret = bmesh.ops.extrude_discrete_faces(bm, faces=[face])

        extruded_face = ret['faces'][0]
        del ret

        # apply rotation to the normal
        rot_mat = matrix_world.copy().to_3x3().normalized()
        normal_world = rot_mat * extruded_face.normal
        normal_world = normal_world * depth

        bmesh.ops.translate(bm,
                            vec=normal_world,
                            space=matrix_world,
                            verts=extruded_face.verts)

        bpy.ops.mesh.select_all(action="DESELECT")
        extruded_face.select = True

    # Extrude and translate an edge of the face to set it sloped
    def __set_face_sloped(self,
                          depth,
                          bm,
                          matrix_world,
                          face,
                          still_edge_tangent):

        # Extrude face
        ret = bmesh.ops.extrude_discrete_faces(bm, faces=[face])

        extruded_face = ret['faces'][0]
        del ret

        # apply rotation to the normal
        rot_mat = matrix_world.copy().to_3x3().normalized()
        normal_world = rot_mat * extruded_face.normal
        normal_world = normal_world * depth

        # Find vertices to be translated
        verts_to_translate = []

        for edge in extruded_face.edges:
            for loop in edge.link_loops:
                if loop.face == extruded_face:
                    tangent = edge.calc_tangent(loop)
                    angle = tangent.angle(still_edge_tangent)
                    if nearly_equal(angle, pi):
                        for vert in edge.verts:
                            verts_to_translate.append(vert)
                        break
                if len(verts_to_translate) > 0:
                    break
            if len(verts_to_translate) > 0:
                break

        bmesh.ops.translate(bm,
                            vec=normal_world,
                            space=matrix_world,
                            verts=verts_to_translate)

    # resize centered faces
    # TODO: use bmesh instead of bpy.ops
    def __resize_faces(self, faces, side_tangent, scale_factor):

        bpy.ops.mesh.select_all(action="DESELECT")
        for faceToResize in faces:
            faceToResize.select = True

        vector_abs(side_tangent)
        resize_value = side_tangent * scale_factor

        bpy.ops.transform.resize(
            value=resize_value,
            constraint_axis=constraint_axis_from_tangent(side_tangent),
            constraint_orientation='LOCAL')

    # Raise a haunched tenon
    def __raise_haunched_tenon(self,
                               bm,
                               matrix_world,
                               tenon,
                               face_to_be_transformed,
                               height_shoulder):
        builder_properties = self.builder_properties
        height_properties = builder_properties.height_properties
        if height_properties.haunch_angle == "sloped":
            still_edge_tangent = \
                face_to_be_transformed.shortest_side_tangent
            if height_properties.reverse_shoulder:
                still_edge_tangent.negate()
            self.__set_face_sloped(height_properties.haunch_depth_value,
                                   bm,
                                   matrix_world,
                                   height_shoulder.face,
                                   still_edge_tangent)
        else:
            self.__set_face_depth(height_properties.haunch_depth_value,
                                  bm,
                                  matrix_world,
                                  height_shoulder.face)
        self.__raise_simple_tenon(bm, matrix_world, tenon)

    # Raise a not haunched tenon
    def __raise_simple_tenon(self, bm, matrix_world, tenon):
        depth = self.builder_properties.depth_value
        self.__set_face_depth(depth,
                              bm,
                              matrix_world,
                              tenon.face)


    def create(self, bm, matrix_world):
        face_to_be_transformed = self.face_to_be_transformed
        builder_properties = self.builder_properties
        thickness_properties = builder_properties.thickness_properties
        height_properties = builder_properties.height_properties

        # Subdivide face
        subdivided_faces = face_to_be_transformed.subdivide_face(
            bm,
            height_properties,
            thickness_properties)

        # Find tenon face (face containing median center)
        if len(subdivided_faces) == 0:
            # when max height centered and max thickness centered
            # (stupid choice but should handle this case too...)
            tenon = TenonFace(face_to_be_transformed.face)

        for f in subdivided_faces:
            if bmesh.geometry.intersect_face_point(
                    f,
                    face_to_be_transformed.median):
                tenon = TenonFace(f)
                break

        # Find faces to be resized
        tenon.find_adjacent_faces(face_to_be_transformed,
                                  height_properties,
                                  thickness_properties)

        # Set tenon shoulder on height side
        if not height_properties.centered:
            height_shoulder = ShoulderFace()
            height_shoulder.get_from_tenon(
                tenon,
                tenon.thickness_faces,
                height_properties.reverse_shoulder,
                face_to_be_transformed.shortest_edges)

            height_shoulder_verts_to_translate = \
                height_shoulder.find_verts_to_translate(
                    face_to_be_transformed.longest_side_tangent,
                    tenon.height_faces)

            translate_vector = height_shoulder.compute_translation_vector(
                height_properties.shoulder_value,
                matrix_world)

            bmesh.ops.translate(
                bm,
                vec=translate_vector,
                space=matrix_world,
                verts=list(height_shoulder_verts_to_translate))

        # Set tenon shoulder on width side
        if not thickness_properties.centered:
            thickness_shoulder = ShoulderFace()
            thickness_shoulder.get_from_tenon(
                tenon,
                tenon.height_faces,
                thickness_properties.reverse_shoulder,
                face_to_be_transformed.longest_edges)

            thickness_shoulder_verts_to_translate = \
                thickness_shoulder.find_verts_to_translate(
                    face_to_be_transformed.shortest_side_tangent,
                    tenon.thickness_faces)

            translate_vector = thickness_shoulder.compute_translation_vector(
                thickness_properties.shoulder_value,
                matrix_world)

            bmesh.ops.translate(
                bm,
                vec=translate_vector,
                space=matrix_world,
                verts=list(thickness_shoulder_verts_to_translate))

        # Set tenon thickness
        if thickness_properties.type != "max":
            scale_factor = tenon.get_scale_factor(
                tenon.thickness_reference_edge,
                matrix_world,
                thickness_properties.value)

            if thickness_properties.centered:
                # centered
                self.__resize_faces(
                    tenon.thickness_faces,
                    face_to_be_transformed.longest_side_tangent,
                    scale_factor)
            else:
                # shouldered
                verts_to_translate = tenon.find_verts_to_translate(
                    tenon.thickness_faces,
                    thickness_shoulder_verts_to_translate)

                translate_vector = \
                    tenon.compute_translation_vector_given_shoulder(
                        tenon.thickness_reference_edge,
                        thickness_shoulder,
                        scale_factor,
                        matrix_world)

                bmesh.ops.translate(bm,
                                    vec=translate_vector,
                                    space=matrix_world,
                                    verts=list(verts_to_translate))

        # Set tenon height
        if height_properties.type != "max":
            scale_factor = tenon.get_scale_factor(
                tenon.height_reference_edge,
                matrix_world,
                height_properties.value)

            if height_properties.centered:
                # centered
                self.__resize_faces(
                    tenon.height_faces,
                    face_to_be_transformed.shortest_side_tangent,
                    scale_factor)
            else:
                # shouldered
                verts_to_translate = tenon.find_verts_to_translate(
                    tenon.height_faces,
                    height_shoulder_verts_to_translate)

                translate_vector = \
                    tenon.compute_translation_vector_given_shoulder(
                        tenon.height_reference_edge,
                        height_shoulder,
                        scale_factor,
                        matrix_world)

                bmesh.ops.translate(bm,
                                    vec=translate_vector,
                                    space=matrix_world,
                                    verts=list(verts_to_translate))

        # Raise tenon
        if not height_properties.centered and height_properties.haunched:
            self.__raise_haunched_tenon(bm, matrix_world, tenon)
        else:
            self.__raise_simple_tenon(bm, matrix_world, tenon)