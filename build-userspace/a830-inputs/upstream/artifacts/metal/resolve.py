#!/usr/bin/env python3
"""把试合并的 7 个冲突文件按报告里的方案解开（只动草稿目录）。"""
import re, subprocess, sys
from pathlib import Path
R = Path(sys.argv[1])
def hunks(text):
    return list(re.finditer(r'^<<<<<<< HEAD\n(.*?)^=======\n(.*?)^>>>>>>> origin/main\n', text, re.M | re.S))
def resolve(path, choices):
    p = R / path; text = p.read_text()
    found = hunks(text)
    assert len(found) == len(choices), (path, len(found), len(choices))
    out = []; at = 0
    for m, c in zip(found, choices):
        out.append(text[at:m.start()])
        ours, theirs = m.group(1), m.group(2)
        out.append(c(ours, theirs) if callable(c) else {'ours': ours, 'theirs': theirs, 'both': ours + theirs}[c])
        at = m.end()
    out.append(text[at:])
    p.write_text(''.join(out))
def sub(path, old, new, count=1):
    p = R / path; text = p.read_text()
    assert old in text, (path, old[:60])
    p.write_text(text.replace(old, new) if count == 0 else text.replace(old, new, count))
# ---- 1/2. 显示路径：两个头文件整个取我们的（带状态带、翻页；半浮点转换只服务 iPadOS 27）
base = sys.argv[2]
for f in ('GpuScanoutClient.h', 'GpuScanoutPresenter.h'):
    (R / f).write_bytes(subprocess.check_output(['git', '-C', base, 'show', 'ours-base:' + f]))
# ---- 3. PrimeShaders.h
resolve('PrimeShaders.h', [lambda o, t: o.replace('native_prime_made, native_prime_rate;}', 'native_prime_made, native_prime_rate,\n               native_prime_functions_resumed;}')])
sub('PrimeShaders.h', '''                        if (ok) {
                            d.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA16Float;''', '''#if defined(__arm64e__)   // iPadOS 27's RGhA framebuffer; on 17 the render server draws BGRA8
                        if (ok) {
                            d.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA16Float;''')
sub('PrimeShaders.h', '''                            if ([device newRenderPipelineStateWithDescriptor:d error:&half]) ++halfMade;
                        }
''', '''                            if ([device newRenderPipelineStateWithDescriptor:d error:&half]) ++halfMade;
                        }
#endif
''')
# ---- 4/5. 桥接：字段取上游的名字和 resolve_filter；错误码取上游带 FailureOrigin 过滤的版本
resolve('bridge/NativeMetalObjects.h', ['theirs', 'theirs'])
resolve('bridge/NativeMetalObjects.rs', ['theirs'])
sub('bridge/NativeMetalObjects.rs', '''/// Metal's `MTLCommandBufferError` code of the command buffer's failure (`CommandBufferError::metal_code`: 11 for a lost device), 0 before
/// one or for a command buffer that did not fail. The frontend ends the render server after its GPU context is lost on this code.
#[unsafe(no_mangle)]
pub extern "C" fn nmb_command_buffer_metal_error_code(command_buffer: *const NmbCommandBuffer) -> u64 {
    guard(0, || Ok(get(command_buffer)?.0.error().map_or(0, |error| error.metal_code())))
}

''', '')
sub('bridge/NativeMetalObjects.rs', '''fn resolve_of(a: &NmbPassAttachment) -> Result<Option<Texture>, String> {
    if a.resolve.is_null() { Ok(None) } else { Ok(Some(get(a.resolve)?.0.clone())) }
}

''', '')
sub('bridge/NativeMetalObjects.rs', '''            slot.resolve_texture = resolve_of(a)?;
            slot.resolve_level = a.resolve_level;
            slot.resolve_slice = a.resolve_slice;
            slot.resolve_depth_plane = a.resolve_depth_plane;
''', '')
sub('bridge/NativeMetalObjects.rs', '''        depth.resolve_texture = resolve_of(&d.depth)?;
        depth.resolve_level = d.depth.resolve_level;
        depth.resolve_slice = d.depth.resolve_slice;
        depth.resolve_depth_plane = d.depth.resolve_depth_plane;
''', '')
sub('bridge/NativeMetalObjects.rs', '''        stencil.resolve_texture = resolve_of(&d.stencil)?;
        stencil.resolve_level = d.stencil.resolve_level;
        stencil.resolve_slice = d.stencil.resolve_slice;
        stencil.resolve_depth_plane = d.stencil.resolve_depth_plane;
''', '')
# ---- 6. 插件
def client_failure(o, t):
    return ('        clientreport::problemLine(text, sizeof text, getprogname(), step, detail);\n        streamText7(*gpu, text);\n' + t +
            '        // 上面的延迟块还要用 gpu：只有没排长文时才释放\n        if (strcmp(step, "problem") || length <= 46) delete gpu;\n')
resolve('NativeAdrenoMetalPlugin.mm', ['ours', 'both', lambda o, t: o, client_failure, 'ours',
                                        lambda o, t: o, 'ours'])
sub('NativeAdrenoMetalPlugin.mm', '        const bool diag = getenv("SHENG_DIAG") != nullptr;\n        if (diag) pthread_create(&monitor,',
    '        pthread_create(&monitor,')
# ---- 7. 前端
F = 'NativeBridgeMetalFrontend.mm'
def texture_bind(o, t):
    return (' [self dumpBind:texture index:index stage:stage+4];\n'
            ' if(texture && [(NBTexture *)texture surface]) {\n  [_commandBuffer.surfaces addObject:texture];\n'
            '  // A binding does not say whether the shader writes; a texture made shader-writable may be (MTLTextureUsageShaderWrite).\n'
            '  if([(NBTexture *)texture descriptor].usage&MTLTextureUsageShaderWrite)[_commandBuffer.writtenSurfaces addObject:texture];\n }\n'
            ' if(texture && gWatchSources)[_commandBuffer watchTexture:(NBTexture *)texture];\n'
            ' ENCODE([self check:nmb_encoder_set_texture(self->_handle,stage,texture?[(NBTexture *)texture handle]:nullptr,(uint32_t)index) what:"setTexture"]);\n')
def resolve_attachment(o, t):
    return ('  // The resolve texture a resolving store action writes. It is written like the attachment itself, so a surface behind it is maintained the same way.\n'
            '  if(id resolveObject=a.resolveTexture) {\n   if(![resolveObject isKindOfClass:[NBTexture class]])return false;\n'
            '   NBTexture *resolve=(NBTexture *)resolveObject;\n   [_resourceOwners addObject:resolve];if(resolve.surface){[_surfaces addObject:resolve];[_writtenSurfaces addObject:resolve];}\n'
            '   out.resolve_texture=resolve.handle;out.resolve_level=a.resolveLevel;\n   out.resolve_slice=a.resolveSlice;out.resolve_depth_plane=a.resolveDepthPlane;\n')
def finish(o, t):
    return t + ' // Every command buffer that ends in error, refused here or failed on the GPU: RenderBox shows nothing for such a frame (its image queue\n // already holds the drawable), so a client\'s report counts them (native_bridge_metal_counts).\n if(self.status==MTLCommandBufferStatusError)gCompletedErrors++;\n'
def counters(o, t):
    return t.replace('gCompleted,gWaitTimeouts;', 'gCompleted,gCompletedErrors,gWaitTimeouts;')
resolve(F, [lambda o, t: t, counters, 'theirs', 'theirs', 'ours', texture_bind,
            lambda o, t: t.replace('count:2])return;', 'count:2 written:2])return;'),
            lambda o, t: t.replace('[self textures:NB_OBJECTS(d) count:1])return;', '[self textures:NB_OBJECTS(d) count:1 written:1])return;')
                          .replace('![self textures:NB_OBJECTS(s) count:1] ||', '![self textures:NB_OBJECTS(s) count:1 written:0] ||')
                          .replace('[self textures:NB_OBJECTS(t) count:1])return;', '[self textures:NB_OBJECTS(t) count:1 written:1])return;'),
            resolve_attachment, finish,
            lambda o, t: t.replace('flushSurfaces(_surfaces);', 'cleanSurfaces(_surfaces,_writtenSurfaces);'),
            'ours'])
# 上游的 familyForCaller 安装段：与我们的 flagArgAnswer 重复
sub(F, ''' if(Method family=class_getInstanceMethod(cls,@selector(supportsFamily:))) {
  gFamilyAnswer=(BOOL (*)(id,SEL,NSInteger))method_getImplementation(family);
  method_setImplementation(family,(IMP)familyForCaller);
 }
''', '')
