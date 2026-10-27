# SPDX-License-Identifier: GPL-3.0-or-later
"""Turn a stored frame into a drawable texture: the GPU side of the packed pixel format.

Frames are stored as one float32 per pixel with R, G and B in its low 24 bits (see
store.pack_pixels). They upload to an R32F texture bit for bit, and this shader unpacks
them into an ordinary RGBA8 picture.
"""

import gpu
from gpu_extras.batch import batch_for_shader

# "packed" is a reserved word in OpenGL GLSL, hence "packed_rgb".
_DECODE_VERT = "void main() { uv = pos * 0.5 + 0.5; gl_Position = vec4(pos, 0.0, 1.0); }"
_DECODE_FRAG = """
void main()
{
  ivec2 texel = ivec2(uv * vec2(textureSize(packed_rgb, 0)));
  uint bits = floatBitsToUint(texelFetch(packed_rgb, texel, 0).r);
  frag_color = vec4(float(bits & 0xFFu), float((bits >> 8u) & 0xFFu), float((bits >> 16u) & 0xFFu), 255.0) / 255.0;
}
"""

_decoder = None  # (shader, batch), built on first use because it needs a GPU context


def _decoder_shader():
    global _decoder
    if _decoder is None:
        interface = gpu.types.GPUStageInterfaceInfo("cache_play_decode_iface")
        interface.smooth('VEC2', "uv")
        info = gpu.types.GPUShaderCreateInfo()
        info.sampler(0, 'FLOAT_2D', "packed_rgb")
        info.vertex_in(0, 'VEC2', "pos")
        info.vertex_out(interface)
        info.fragment_out(0, 'VEC4', "frag_color")
        info.vertex_source(_DECODE_VERT)
        info.fragment_source(_DECODE_FRAG)
        shader = gpu.shader.create_from_info(info)
        batch = batch_for_shader(
            shader, 'TRIS', {"pos": ((-1, -1), (1, -1), (1, 1), (-1, 1))}, indices=((0, 1, 2), (0, 2, 3)),
        )
        _decoder = (shader, batch)
    return _decoder


class Display:
    """The frame on screen, unpacked into an RGBA8 offscreen buffer."""

    def __init__(self):
        self._offscreen = None
        self._shown = None  # (cache generation, frame)

    def reset(self):
        self._offscreen = None
        self._shown = None

    def prepare(self, cache, frame):
        """Unpack frame so it is ready to draw. Call outside region drawing (it binds its own
        framebuffer, which must not happen in the middle of the 3D Viewport's scene pass).

        Keeps the previous frame when this one cannot be loaded in time.
        """
        tag = (cache.generation, frame)
        if tag != self._shown:
            pixels = cache.get(frame)
            if pixels is not None:
                self._upload(cache.width, cache.height, pixels)
                self._shown = tag

    def texture(self, cache):
        """The prepared texture, or None if nothing from this cache is prepared."""
        if self._shown is None or self._shown[0] != cache.generation:
            return None
        return self._offscreen.texture_color

    def _upload(self, width, height, pixels):
        offscreen = self._offscreen
        if offscreen is None or (offscreen.width, offscreen.height) != (width, height):
            offscreen = self._offscreen = gpu.types.GPUOffScreen(width, height, format='RGBA8')
            offscreen.texture_color.filter_mode(True)
        packed = gpu.types.GPUTexture(
            (width, height), format='R32F', data=gpu.types.Buffer('FLOAT', pixels.size, pixels),
        )
        shader, batch = _decoder_shader()
        with offscreen.bind():
            gpu.state.blend_set('NONE')
            shader.uniform_sampler("packed_rgb", packed)
            batch.draw(shader)


display = Display()


def unregister():
    global _decoder
    _decoder = None
    display.reset()
