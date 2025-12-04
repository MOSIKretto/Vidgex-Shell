from fabric import Property, Signal
from fabric.widgets.widget import Widget

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from OpenGL.GL.shaders import compileProgram, compileShader
import OpenGL.GL as GL

from enum import Enum


class ShadertoyUniformType(Enum):
    FLOAT = 1
    INTEGER = 2
    VECTOR = 3
    TEXTURE = 4

class ShadertoyCompileError(Exception):
    pass

class Shadertoy(Gtk.GLArea, Widget):
    @Signal
    def ready(self):
        pass

    @Property(str, "read-write")
    def shader_buffer(self):
        return self._shader_buffer

    @shader_buffer.setter
    def shader_buffer(self, shader_buffer):
        self._shader_buffer = shader_buffer
        if not self._ready:
            return
        self._shader_uniforms.clear()
        self.do_realize()
        self.queue_draw()

    DEFAULT_VERTEX_SHADER = """
    in vec2 position;

    void main() {
        gl_Position = vec4(position, 0.0, 1.0);
    }
    """

    DEFAULT_FRAGMENT_UNIFORMS = """
    uniform vec3 iResolution;
    uniform float iTime;
    uniform float iTimeDelta;
    uniform float iFrameRate;
    uniform int iFrame;
    uniform float iChannelTime[4];
    uniform vec3 iChannelResolution[4];
    uniform vec4 iMouse;
    uniform sampler2D iChannel0;
    uniform sampler2D iChannel1;
    uniform sampler2D iChannel2;
    uniform sampler2D iChannel3;
    uniform vec4 iDate;
    uniform float iSampleRate;
    """

    FRAGMENT_MAIN_FUNCTION = """
    void main() {
        mainImage(gl_FragColor, gl_FragCoord.xy);
    }
    """

    def __init__(
        self,
        shader_buffer,
        shader_uniforms=None,
        name=None,
        visible=True,
        all_visible=False,
        style=None,
        style_classes=None,
        tooltip_text=None,
        tooltip_markup=None,
        h_align=None,
        v_align=None,
        h_expand=False,
        v_expand=False,
        size=None,
        **kwargs,
    ):
        Gtk.GLArea.__init__(self)
        Widget.__init__(
            self,
            name,
            visible,
            all_visible,
            style,
            style_classes,
            tooltip_text,
            tooltip_markup,
            h_align,
            v_align,
            h_expand,
            v_expand,
            size,
            **kwargs,
        )
        self._shader_buffer = shader_buffer
        if shader_uniforms is None:
            self._shader_uniforms = []
        else:
            self._shader_uniforms = shader_uniforms

        self.set_required_version(3, 3)
        self.set_has_depth_buffer(False)
        self.set_has_stencil_buffer(False)

        self._ready = False
        self._program = None
        self._vao = None
        self._quad_vbo = None
        self._texture_units = {}

        self._start_time = GLib.get_monotonic_time() / 1e6
        self._frame_time = self._start_time
        self._frame_count = 0

        def tick_callback(*_):
            self.queue_draw()
            return True

        self._tick_id = self.add_tick_callback(tick_callback)

    def do_bake_program(self):
        try:
            vertex_shader = compileShader(
                self.DEFAULT_VERTEX_SHADER, GL.GL_VERTEX_SHADER
            )
            fragment_source = (
                self.DEFAULT_FRAGMENT_UNIFORMS
                + self._shader_buffer
                + self.FRAGMENT_MAIN_FUNCTION
            )
            fragment_shader = compileShader(
                fragment_source,
                GL.GL_FRAGMENT_SHADER,
            )
        except Exception as e:
            raise ShadertoyCompileError(
                f"couldn't compile the provided shader, OpenGL error:\n {e}"
            )

        return compileProgram(vertex_shader, fragment_shader)

    def do_realize(self, *_):
        Gtk.GLArea.do_realize(self)
        if not self._ready:
            ctx = self.get_context()
            err = self.get_error()
            if err or not ctx:
                error_msg = err or 'context is None'
                raise RuntimeError(
                    f"couldn't initialize the drawing context, error: {error_msg}"
                )

            ctx.make_current()

        if self._program:
            GL.glDeleteProgram(self._program)
            self._program = None
        self._program = self.do_bake_program()

        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)

        self._quad_vbo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._quad_vbo)

        quad_verts = (-1.0, -1.0, 1.0, -1.0, -1.0, 1.0, 1.0, 1.0)
        array_type = GL.GLfloat * len(quad_verts)

        GL.glBufferData(
            GL.GL_ARRAY_BUFFER,
            len(quad_verts) * 4,
            array_type(*quad_verts),
            GL.GL_STATIC_DRAW,
        )

        self._vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(self._vao)

        position = GL.glGetAttribLocation(self._program, "position")
        GL.glEnableVertexAttribArray(position)
        GL.glVertexAttribPointer(position, 2, GL.GL_FLOAT, GL.GL_FALSE, 0, None)

        for uniform in self._shader_uniforms:
            uname = uniform[0]
            utype = uniform[1]
            uvalue = uniform[2]
            self.set_uniform(uname, utype, uvalue)

        self._ready = True
        self.ready()

    def do_get_timing(self):
        current_time = GLib.get_monotonic_time() / 1e6
        delta_time = current_time - self._frame_time
        if delta_time > 0:
            frame_rate = 1.0 / delta_time
        else:
            frame_rate = 0.0
        return current_time, delta_time, frame_rate

    def do_post_render(self, time):
        self._frame_time = time
        self._frame_count += 1

    def do_render(self, ctx):
        if not self._program:
            if self._tick_id:
                self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
            return False

        GL.glUseProgram(self._program)

        GL.glClear(GL.GL_COLOR_BUFFER_BIT)

        alloc = self.get_allocation()
        width = alloc.width
        height = alloc.height
        mouse_pos = self.get_pointer()

        current_time, delta_time, frame_rate = self.do_get_timing()

        time_uniform = current_time - self._start_time
        self.set_uniform("iTime", ShadertoyUniformType.FLOAT, time_uniform)
        self.set_uniform("iFrame", ShadertoyUniformType.INTEGER, self._frame_count)
        self.set_uniform("iTimeDelta", ShadertoyUniformType.FLOAT, delta_time)
        self.set_uniform("iFrameRate", ShadertoyUniformType.FLOAT, frame_rate)
        self.set_uniform(
            "iResolution", ShadertoyUniformType.VECTOR, (width, height, 1.0)
        )
        mouse_x = mouse_pos[0]
        mouse_y = height - mouse_pos[1]
        self.set_uniform(
            "iMouse",
            ShadertoyUniformType.VECTOR,
            (mouse_x, mouse_y, 0, 0),
        )

        GL.glBindVertexArray(self._vao)
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
        self.do_post_render(current_time)
        return True

    def do_resize(self, width, height):
        Gtk.GLArea.do_resize(self, width, height)
        GL.glViewport(0, 0, width, height)

    def set_uniform(self, name, uniform_type, value):
        if not self._program:
            raise RuntimeError("the shader program is not initialized")
        GL.glUseProgram(self._program)
        location = GL.glGetUniformLocation(self._program, name)
        
        if uniform_type == ShadertoyUniformType.VECTOR:
            vector_length = len(value)
            if vector_length == 2:
                GL.glUniform2f(location, value[0], value[1])
            elif vector_length == 3:
                GL.glUniform3f(location, value[0], value[1], value[2])
            elif vector_length == 4:
                GL.glUniform4f(location, value[0], value[1], value[2], value[3])
        elif uniform_type == ShadertoyUniformType.FLOAT:
            GL.glUniform1f(location, value)
        elif uniform_type == ShadertoyUniformType.INTEGER:
            GL.glUniform1i(location, value)
        elif uniform_type == ShadertoyUniformType.TEXTURE:
            pixbuf = value.flip(False)
            if pixbuf.get_has_alpha():
                texture_format = GL.GL_RGBA
            else:
                texture_format = GL.GL_RGB

            if name not in self._texture_units:
                texture = GL.glGenTextures(1)
                texture_unit = len(self._texture_units)
                self._texture_units[name] = (texture_unit, texture)
            else:
                texture_unit, texture = self._texture_units[name]

            GL.glActiveTexture(GL.GL_TEXTURE0 + texture_unit)
            GL.glBindTexture(GL.GL_TEXTURE_2D, texture)

            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_REPEAT)
            GL.glTexParameteri(
                GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR
            )
            GL.glTexParameteri(
                GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR
            )

            GL.glTexImage2D(
                GL.GL_TEXTURE_2D,
                0,
                texture_format,
                pixbuf.get_width(),
                pixbuf.get_height(),
                0,
                texture_format,
                GL.GL_UNSIGNED_BYTE,
                pixbuf.get_pixels(),
            )
            GL.glGenerateMipmap(GL.GL_TEXTURE_2D)

            GL.glUniform1i(location, texture_unit)