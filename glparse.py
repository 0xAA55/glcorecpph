#!/usr/bin/env python3
# -*- coding: utf-8 -*
import io
import json
import xml.etree.ElementTree as ET

prefix = 'gl'
PREFIX = prefix.upper()
prefix_ = f'{prefix}_'
PREFIX_ = f'{PREFIX}_'
PREFIX_ES_ = f'{PREFIX}_ES_'
modname = 'glcore'
rust_derive = '#[derive(Clone, Copy, PartialEq, Eq, Hash)]'
rust_derive_global = '#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]'
already_defined = set()

def do_parse_glxml(glxmlfile):
	group_data = {}
	enums_data = {}
	funcs_data = {}

	registry = ET.parse('gl.xml').getroot()
	for enums in registry.iter('enums'):
		try:
			if enums.attrib['namespace'] != 'GL': continue
		except KeyError:
			continue
		for enum in enums.iter('enum'):
			enumname = enum.attrib['name']
			enumvalue = enum.attrib['value']
			try:
				enumgroups = enum.attrib['group'].split(',')
			except KeyError:
				enumgroups = []
			enums_data[enumname] = {'value': enumvalue, 'group': enumgroups, 'type': {}}
			for enumgroup in enumgroups:
				if not enumgroup in group_data: group_data[enumgroup] = []
				group_data[enumgroup] += [enumname]

	commands = next(registry.iter('commands'))
	def parsecmd(parsetagtype, tag):
		valtype = []
		valname = ''
		def ontag(p):
			nonlocal valtype
			if p.text is not None:
				valtype += [p.text.strip()]
			return False
		def onptype(p):
			nonlocal valtype
			valtype += [p.text.strip()]
			if p.tail is not None:
				valtype += [p.tail.strip()]
			return False
		def onname(p):
			nonlocal valname
			valname = p.text
			return True
		tagproc = {
				parsetagtype: ontag,
				'ptype': onptype,
				'name': onname,
			}
		for p in tag.iter():
			if tagproc[p.tag](p): break
		valtype = ' '.join(valtype).replace('  ', ' ').strip()
		return valtype, valname
	groupname_used_in_args = set()
	for command in commands:
		proto = command[0]
		retval, funcname = parsecmd('proto', proto)

		arglist = []
		for param in command.iter('param'):
			pt = param.attrib
			argtype, argname = parsecmd('param', param)
			argdata = {'type': argtype, 'name': argname}
			try:
				groupname = pt['group']
				groupname_used_in_args |= {groupname}
			except KeyError:
				groupname = None
			argdata['group'] = groupname
			arglist += [argdata]
			if groupname is None:
				continue
			for enumname in group_data[groupname]:
				try:
					enums_data[enumname]['type'][argtype] |= {groupname}
				except KeyError:
					enums_data[enumname]['type'][argtype] = {groupname}
		funcs_data[funcname] = {
			'return': retval,
			'params': arglist
		}
		#print(f'{retval} {funcname} ({", ".join(["%s %s" % (arg["type"], arg["name"]) for arg in arglist])});')

	groupname_not_used_in_args = set(group_data.keys()) - groupname_used_in_args
	#print('\n'.join(sorted(list(groupname_not_used_in_args))))

	normal_groups = {}
	special_groups = set()
	designated_grouptypes = {
		'TextureWrapMode': 'GLint',
		'TextureMagFilter': 'GLint',
		'TextureMinFilter': 'GLint',
		#'InternalFormat': 'GLint',
		#'SizedInternalFormat': 'GLint',
	}
	for groupname in groupname_not_used_in_args:
		if groupname.endswith('Mask'):
			designated_grouptypes[groupname] = 'GLbitfield'
	for enumname, enumdata in enums_data.items():
		enumtype = enumdata['type']
		if len(enumtype) == 1:
			for at, gs in enumtype.items():
				for g in gs:
					normal_groups[g] = at
		elif len(enumtype) > 1:
			for at, g in enumtype.items():
				special_groups |= g
	for enumname in enums_data:
		enums_data[enumname]['type'] = 'GLenum'
	for groupname, grouptype in normal_groups.items():
		for enumname in group_data[groupname]:
			enums_data[enumname]['type'] = grouptype
	for groupname in special_groups:
		for enumname in group_data[groupname]:
			enums_data[enumname]['type'] = 'GLenum'
	for groupname, grouptype in designated_grouptypes.items():
		for enumname in group_data[groupname]:
			enums_data[enumname]['type'] = grouptype
	parsed = {
		'enums': enums_data,
		'funcs': funcs_data
	}
	with open('glcore.json', 'w', encoding='utf-8') as f:
		json.dump(parsed, f, indent=4)
	return parsed

def _is_block_begin(line, PREFIX_):
	return \
		line.startswith(f'#ifndef {PREFIX_}') or \
		line.startswith(f'#ifndef {PREFIX_ES_}')

def _is_block_end(line, version_name, PREFIX_):
	return \
		line == f'#endif /* {PREFIX_}{version_name} */' or \
		line == f'#endif /* {PREFIX_ES_}{version_name} */'

def _dismantle_typedef(line):
	line = line.replace('}', '} ').replace(' *', '*').replace('*', '* ').replace('* * ', '** ').replace('  ', ' ')
	try:
		rettype, rest = line.split('(', 1)
	except ValueError:
		# non function
		line = line.replace('* ', '*').replace('*', ' *').replace(' * *', ' **').replace('  ', ' ')
		try:
			first, more = line.split(',', 1)
		except ValueError:
			first, more = line, ""
		target_type, typealias = first.rsplit(' ', 1)
		typealias = [typealias]
		if len(more): typealias += more.split(',')
		typealias = [a.strip() for a in typealias]
		return {'type': 'typealias', 'target_type': target_type, 'alias': typealias}
	rettype = rettype.strip()
	fntype, arglist = rest.split('(', 1)
	fntype = fntype.strip()
	arglist = arglist.strip()
	if fntype[-1] != ')' or arglist[-1] != ')':
		raise ValueError(f"Expected ')' near `({arglist}`")
	fntype = fntype[:-1].rstrip()
	arglist = arglist[:-1].rstrip()
	calltype, ftname = fntype.rsplit(' ', 1)
	return {'type': 'functype', 'ret': rettype, 'calltype': calltype, 'typename': ftname, 'arglist': arglist}

def _dismantle_proto(line):
	if line.startswith('GLAPI '): line = line[len('GLAPI '):]
	line = line.replace('*', '* ').replace(' *', '*').replace('  ', ' ')
	rettype_calltype_funcname, arglist = line.split('(', 1)
	rettype, calltype, funcname = rettype_calltype_funcname.strip().rsplit(' ', 2)
	arglist = arglist[:-1]
	return {'type': 'funcproto', 'ret': rettype, 'calltype': calltype, 'funcname': funcname, 'arglist': arglist}

def _chew(filename):
	with open(filename, 'r', encoding='utf-8') as f:
		is_in_block = False
		is_in_proto = False
		for line in f:
			line = line.strip()
			if len(line) == 0: continue
			while '  ' in line: line = line.replace('  ', ' ')
			line = line.replace('GL_APICALL ', 'GLAPI ').replace('GL_APIENTRY', 'APIENTRY')
			if not is_in_block:
				if _is_block_begin(line, PREFIX_):
					is_in_block = True
					version_name = line.split('_', 1)[-1]
					yield {'type': 'version', 'id': version_name}
				else:
					print(f'Unknown line: "{line}"')
				continue
			elif _is_block_end(line, version_name, PREFIX_):
				if is_in_proto:
					print('Unexpected end of version')
					is_in_proto = False
				yield {'type': 'version_end', 'id': version_name}
				is_in_block = False
				continue
			if not is_in_proto:
				if line == '#ifdef GL_GLEXT_PROTOTYPES' or line == '#if GL_GLES_PROTOTYPES':
					is_in_proto = True
					continue
				if line.startswith('#define '):
					defi = line.split(' ', 1)[-1]
					if defi == f'{PREFIX_}{version_name} 1':
						continue
					try:
						defn, defv = defi.split(' ', 1)
					except ValueError:
						print(f'Unknown define line: "{line}"')
						continue
					if not defn.startswith(PREFIX_):
						print(f'Definition PREFIX not match: {line}')
						continue
					defn = defn[len(PREFIX_):]
					yield {'type': 'define', 'id': defn, 'value': defv}
					continue
				if line.startswith('typedef '):
					if line[-1] != ';':
						print(f'Expected \';\' at the end of line "{line}"')
						continue
					line = line[:-1]
					try:
						fpdata = _dismantle_typedef(line[7:].strip())
					except ValueError as e:
						print(f'Parse typedef failed: {str(e)}: {line}')
						continue
					yield fpdata
					continue
				print(f'Unknown line: {line}')
			else:
				if line == '#endif':
					is_in_proto = False
					continue
				if not line.startswith('GLAPI '):
					print(f'Unknown line: {line}')
					continue
				if line[-1] != ';':
					print(f'Expected \';\' at the end of line "{line}"')
					continue
				line = line[:-1]
				try:
					protodata = _dismantle_proto(line)
				except ValueError as e:
					print(f'Parse function pointer failed: {str(e)}')
					continue
				yield protodata
				continue
				
def do_parse(parsefiles, glxml):
	enumtype = {enum: enum_data['type'] for enum, enum_data in glxml['enums'].items()}

	overloadables = sorted([
		'TexParameter',
		'PixelStore',
		'GetTexParameter',
		'GetTexLevelParameter',
		'PointParameter',
		'GetQueryObject',
		'Uniform',
		'UniformMatrix',
		'VertexAttrib',
		'GetUniform',
		'GetVertexAttrib',
		'ClearBuffer',
		'SamplerParameter',
		'GetSamplerParameter',
		'PatchParameter',
		'ProgramUniform',
		'ProgramUniformMatrix',
		'ClearNamedFramebuffer',
		'GetNamedBufferParameter',
		'TextureParameter',
		'GetTextureParameter',
		'TextureLevelParameter',
		'GetTextureLevelParameter',
		'GetnUniform'
	], key=len, reverse=True)

	overload_preserve_prefix = {'N', 'I', 'L', 'P'}

	type_abbrs = sorted([
		'b', 's', 'i', 'i64',
		'ub', 'us', 'ui', 'ui64',
		'f', 'd'
	], key=len, reverse=True)

	mat_dims = sorted([
		'1', '2', '3', '4', '4N',
		'2x3', '2x4',
		'3x2', '3x4',
		'4x2', '4x3'
	], key=len, reverse=True)

	cppfunc_cast = "reinterpret_cast"

	csharp_typeconv = {
		'int': 'int',
		'void': 'void',
		'int8_t': 'sbyte',
		'uint8_t': 'byte',
		'int16_t': 'short',
		'uint16_t': 'ushort',
		'int32_t': 'int',
		'uint32_t': 'uint',
		'int64_t': 'long',
		'uint64_t': 'ulong',
		'ptrdiff_t': 'IntPtr',
		'size_t': 'UIntPtr',
		'GLboolean': 'bool',
		'unsigned char': 'byte',
		'unsigned short': 'ushort',
		'unsigned int': 'uint',
		'unsigned long': 'ulong',
		'GLsync' : 'IntPtr',
		'GLDEBUGPROC': 'GLDEBUGPROC',
		'khronos_float_t': 'float',
		'khronos_ssize_t': 'IntPtr',
		'khronos_intptr_t': 'IntPtr',
		'khronos_int8_t': 'sbyte',
		'khronos_uint8_t': 'byte',
		'khronos_int16_t': 'short',
		'khronos_uint16_t': 'ushort',
		'khronos_int32_t': 'int',
		'khronos_uint32_t': 'uint',
		'khronos_int64_t': 'long',
		'khronos_uint64_t': 'ulong',
	}

	csharp_keywords = {
		'params',
		'ref',
		'string',
	}

	parsed = {
		'typealias': {},
		'define': {},
		'functype': {},
		'funcproto': {}
	}
	versions = {}
	version_name = None
	last_version = None
	firstver_name = None
	firstver_classname = None
	rs_traits = []
	rs_global_struct_name = "GLCore"
	OpenGL = 'OpenGL'
	outs_hpp = io.StringIO()
	outs_cpp = io.StringIO()
	outs_csharp = io.StringIO()
	outs_rs = {
		'global': {
			'predef': io.StringIO(),
			'struct': io.StringIO(),
			'impl': io.StringIO(),
			'trait': io.StringIO(),
			'members': [],
		}
	}

	outs_hpp.write('#pragma once\n')
	outs_hpp.write('\n')
	outs_hpp.write('#include<string>\n')
	outs_hpp.write('#include<cstdint>\n')
	outs_hpp.write('#include<cstddef>\n')
	outs_hpp.write('#include<stdexcept>\n')
	outs_hpp.write('\n')
	outs_hpp.write('namespace GL\n')
	outs_hpp.write('{\n')
	outs_hpp.write('#ifndef APIENTRY\n')
	outs_hpp.write('#  if defined(__MINGW32__) || defined(__CYGWIN__) || (_MSC_VER >= 800) || defined(_STDCALL_SUPPORTED) || defined(__BORLANDC__)\n')
	outs_hpp.write('#    define APIENTRY __stdcall\n')
	outs_hpp.write('#  else\n')
	outs_hpp.write('#    define APIENTRY\n')
	outs_hpp.write('#  endif\n')
	outs_hpp.write('#endif\n')
	outs_hpp.write('#ifndef APIENTRYP\n')
	outs_hpp.write('#define APIENTRYP APIENTRY*\n')
	outs_hpp.write('#endif\n')
	outs_hpp.write('\n')
	outs_hpp.write('\tusing Func_GetProcAddress = void*(APIENTRYP)(const char* symbol);\n')
	outs_hpp.write('\tusing khronos_float_t = float;\n')
	outs_hpp.write('\tusing khronos_ssize_t = ptrdiff_t;\n')
	outs_hpp.write('\tusing khronos_intptr_t = ptrdiff_t;\n')
	outs_hpp.write('\tusing khronos_int16_t = int16_t;\n')
	outs_hpp.write('\tusing khronos_int8_t = int8_t;\n')
	outs_hpp.write('\tusing khronos_uint8_t = uint8_t;\n')
	outs_hpp.write('\tusing khronos_uint16_t = uint16_t;\n')
	outs_hpp.write('\tusing khronos_int32_t = int32_t;\n')
	outs_hpp.write('\tusing khronos_int64_t = int64_t;\n')
	outs_hpp.write('\tusing khronos_uint64_t = uint64_t;\n')
	outs_hpp.write('\n')
	outs_hpp.write('\tclass NullFuncPtrException : public std::runtime_error\n')
	outs_hpp.write('\t{\n')
	outs_hpp.write('\tpublic:\n')
	outs_hpp.write('\t\tNullFuncPtrException(std::string what) noexcept;\n')
	outs_hpp.write('\t};\n')
	outs_hpp.write('\n')

	outs_cpp.write(f'#include "{modname}.hpp"\n')
	outs_cpp.write('\n')
	outs_cpp.write('#include<cstring>\n')
	outs_cpp.write('\n')
	outs_cpp.write('#ifndef GLAPI\n')
	outs_cpp.write('#  if defined(__MINGW32__) || defined(__CYGWIN__) || (_MSC_VER >= 800) || defined(_STDCALL_SUPPORTED) || defined(__BORLANDC__)\n')
	outs_cpp.write('#    define GLAPI extern "C" __declspec(dllimport)\n')
	outs_cpp.write('#  else\n')
	outs_cpp.write('#    define GLAPI extern "C"\n')
	outs_cpp.write('#  endif\n')
	outs_cpp.write('#endif\n')
	outs_cpp.write('\n')
	outs_cpp.write('namespace GL\n')
	outs_cpp.write('{\n')
	outs_cpp.write('\tNullFuncPtrException::NullFuncPtrException(std::string what) noexcept:\n')
	outs_cpp.write('\t\tstd::runtime_error(what)\n')
	outs_cpp.write('\t{\n')
	outs_cpp.write('\t}\n')
	outs_cpp.write('\n')
	outs_cpp.write('\tstatic void NullFuncPtr()\n')
	outs_cpp.write('\t{\n')
	outs_cpp.write(f'\t\tthrow NullFuncPtrException("{OpenGL} function pointer is null.\\n");\n')
	outs_cpp.write('\t}\n')
	outs_cpp.write('\n')

	outs_csharp.write('using System;\n')
	outs_csharp.write('using System.Text;\n')
	outs_csharp.write('using System.Runtime.InteropServices;\n')
	outs_csharp.write('namespace GL\n')
	outs_csharp.write('{\n')
	outs_csharp.write('\tpublic class NullOpenGLFunctionPointerException : Exception\n')
	outs_csharp.write('\t{\n')
	outs_csharp.write('\t\tpublic NullOpenGLFunctionPointerException() {}\n')
	outs_csharp.write('\t\tpublic NullOpenGLFunctionPointerException(string message) : base(message) {}\n')
	outs_csharp.write('\t\tpublic NullOpenGLFunctionPointerException(string message, Exception inner) : base(message, inner) {}\n')
	outs_csharp.write('\t}\n')
	outs_csharp.write('\tpublic delegate IntPtr Delegate_GetProcAddress (string ProcName);\n')

	outs_rs['global']['predef'].write('\n')
	outs_rs['global']['predef'].write('#![allow(dead_code)]\n')
	outs_rs['global']['predef'].write('#![allow(non_snake_case)]\n')
	outs_rs['global']['predef'].write('#![allow(non_camel_case_types)]\n')
	outs_rs['global']['predef'].write('#![allow(non_upper_case_globals)]\n')
	outs_rs['global']['predef'].write('#![allow(unpredictable_function_pointer_comparisons)]\n')
	outs_rs['global']['predef'].write("use std::{\n")
	outs_rs['global']['predef'].write("\tmem::transmute,\n")
	outs_rs['global']['predef'].write("\tffi::{c_void, CStr},\n")
	outs_rs['global']['predef'].write("\tfmt::{self, Debug, Formatter},\n")
	outs_rs['global']['predef'].write("\tpanic::catch_unwind,\n")
	outs_rs['global']['predef'].write("\tptr::null,\n")
	outs_rs['global']['predef'].write("};\n")
	outs_rs['global']['predef'].write('\n')
	outs_rs['global']['predef'].write(f'/// The {OpenGL} error type\n')
	outs_rs['global']['predef'].write('#[derive(Debug, Clone, Copy)]\n')
	outs_rs['global']['predef'].write('pub enum GLCoreError {\n')
	outs_rs['global']['predef'].write('\tNullFunctionPointer(&\'static str),\n')
	outs_rs['global']['predef'].write('\tInvalidEnum(&\'static str),\n')
	outs_rs['global']['predef'].write('\tInvalidValue(&\'static str),\n')
	outs_rs['global']['predef'].write('\tInvalidOperation(&\'static str),\n')
	outs_rs['global']['predef'].write('\tInvalidFramebufferOperation(&\'static str),\n')
	outs_rs['global']['predef'].write('\tOutOfMemory(&\'static str),\n')
	outs_rs['global']['predef'].write('\tStackUnderflow(&\'static str),\n')
	outs_rs['global']['predef'].write('\tStackOverflow(&\'static str),\n')
	outs_rs['global']['predef'].write('\tUnknownError((GLenum, &\'static str)),\n')
	outs_rs['global']['predef'].write('}\n')
	outs_rs['global']['predef'].write('\n')
	outs_rs['global']['predef'].write('/// The result returns from this crate. It\'s the alias of `Result<T, GLCoreError>`\n')
	outs_rs['global']['predef'].write('type Result<T> = std::result::Result<T, GLCoreError>;\n')
	outs_rs['global']['predef'].write('\n')
	outs_rs['global']['predef'].write('/// Convert the constants returns from `glGetError()` to `Result<T>`\n')
	outs_rs['global']['predef'].write('pub fn to_result<T>(funcname: &\'static str, ret: T, gl_error: GLenum) -> Result<T> {\n')
	outs_rs['global']['predef'].write('\tmatch gl_error {\n')
	outs_rs['global']['predef'].write('\t\tGL_NO_ERROR => Ok(ret),\n')
	outs_rs['global']['predef'].write('\t\tGL_INVALID_ENUM => Err(GLCoreError::InvalidEnum(funcname)),\n')
	outs_rs['global']['predef'].write('\t\tGL_INVALID_VALUE => Err(GLCoreError::InvalidValue(funcname)),\n')
	outs_rs['global']['predef'].write('\t\tGL_INVALID_OPERATION => Err(GLCoreError::InvalidOperation(funcname)),\n')
	outs_rs['global']['predef'].write('\t\tGL_INVALID_FRAMEBUFFER_OPERATION => Err(GLCoreError::InvalidFramebufferOperation(funcname)),\n')
	outs_rs['global']['predef'].write('\t\tGL_OUT_OF_MEMORY => Err(GLCoreError::OutOfMemory(funcname)),\n')
	outs_rs['global']['predef'].write('\t\tGL_STACK_UNDERFLOW => Err(GLCoreError::StackUnderflow(funcname)),\n')
	outs_rs['global']['predef'].write('\t\tGL_STACK_OVERFLOW => Err(GLCoreError::StackOverflow(funcname)),\n')
	outs_rs['global']['predef'].write('\t\t_ => Err(GLCoreError::UnknownError((gl_error, funcname))),\n')
	outs_rs['global']['predef'].write('\t}\n')
	outs_rs['global']['predef'].write('}\n')
	outs_rs['global']['predef'].write('\n')
	outs_rs['global']['predef'].write('/// Translate the returned `Result<T>` from `std::panic::catch_unwind()` to our `Result<T>`\n')
	outs_rs['global']['predef'].write('pub fn process_catch<T>(funcname: &\'static str, ret: std::thread::Result<T>) -> Result<T> {\n')
	outs_rs['global']['predef'].write('\tmatch ret {\n')
	outs_rs['global']['predef'].write('\t\tOk(ret) => Ok(ret),\n')
	outs_rs['global']['predef'].write('\t\tErr(_) => {\n')
	outs_rs['global']['predef'].write('\t\t\tErr(GLCoreError::NullFunctionPointer(funcname))\n')
	outs_rs['global']['predef'].write('\t\t}\n')
	outs_rs['global']['predef'].write('\t}\n')
	outs_rs['global']['predef'].write('}\n')
	outs_rs['global']['predef'].write('\n')
	outs_rs['global']['predef'].write('/// Alias to `f32`\n')
	outs_rs['global']['predef'].write('pub type khronos_float_t = f32;\n')
	outs_rs['global']['predef'].write('\n')
	outs_rs['global']['predef'].write('/// Alias to `usize`\n')
	outs_rs['global']['predef'].write('pub type khronos_ssize_t = usize;\n')
	outs_rs['global']['predef'].write('\n')
	outs_rs['global']['predef'].write('/// Alias to `usize`\n')
	outs_rs['global']['predef'].write('pub type khronos_intptr_t = usize;\n')
	outs_rs['global']['predef'].write('\n')
	outs_rs['global']['predef'].write('/// Alias to `i16`\n')
	outs_rs['global']['predef'].write('pub type khronos_int16_t = i16;\n')
	outs_rs['global']['predef'].write('\n')
	outs_rs['global']['predef'].write('/// Alias to `i8`\n')
	outs_rs['global']['predef'].write('pub type khronos_int8_t = i8;\n')
	outs_rs['global']['predef'].write('\n')
	outs_rs['global']['predef'].write('/// Alias to `u8`\n')
	outs_rs['global']['predef'].write('pub type khronos_uint8_t = u8;\n')
	outs_rs['global']['predef'].write('\n')
	outs_rs['global']['predef'].write('/// Alias to `i32`\n')
	outs_rs['global']['predef'].write('pub type khronos_int32_t = i32;\n')
	outs_rs['global']['predef'].write('\n')
	outs_rs['global']['predef'].write('/// Alias to `u16`\n')
	outs_rs['global']['predef'].write('pub type khronos_uint16_t = u16;\n')
	outs_rs['global']['predef'].write('\n')
	outs_rs['global']['predef'].write('/// Alias to `i64`\n')
	outs_rs['global']['predef'].write('pub type khronos_int64_t = i64;\n')
	outs_rs['global']['predef'].write('\n')
	outs_rs['global']['predef'].write('/// Alias to `u64`\n')
	outs_rs['global']['predef'].write('pub type khronos_uint64_t = u64;\n')
	outs_rs['global']['predef'].write('\n')
	outs_rs['global']['struct'].write(f'/// All of the {OpenGL} functions\n')
	outs_rs['global']['struct'].write(f'{rust_derive_global}\n')
	outs_rs['global']['struct'].write(f'pub struct {rs_global_struct_name} {{\n')

	def rs_type_conv(cpptype):
		try:
			return {
				'void': 'c_void',
				'c_void': 'c_void',
				'int8_t' : 'i8',
				'int16_t': 'i16',
				'int32_t': 'i32',
				'int64_t': 'i64',
				'uint8_t' : 'u8',
				'uint16_t': 'u16',
				'uint32_t': 'u32',
				'uint64_t': 'u64',
				'int': 'i32',
				'short': 'i16',
				'char': 'i8',
				'unsigned': 'u32',
				'unsigned int': 'u32',
				'unsigned short': 'u16',
				'unsigned char': 'u8',
				'float': 'f32',
				'double': 'f64',
				'*mut __GLsync': '*mut c_void',
			}[cpptype]
		except KeyError as e:
			if cpptype not in {
				"khronos_float_t",
				"khronos_ssize_t",
				"khronos_intptr_t",
				"khronos_int16_t",
				"khronos_int8_t",
				"khronos_uint8_t",
				"khronos_int32_t",
				"khronos_uint16_t",
				"khronos_int64_t",
				"khronos_uint64_t",
			}:
				print(f'Unknown cpp type for rust: {e}')
			return cpptype

	def rs_keyword_rename(ident):
		keywords = {
			"type",
			"ref",
			"string",
		}
		if ident in keywords:
			ident += '_'
		return ident;

	def rs_argtype_conv(argt):
		ret = ''
		is_const = False
		if argt.startswith('const '):
			is_const = True
			argt = argt[6:]
		tokens = [arg.strip() for arg in argt.split('*')]
		while len(tokens) > 1:
			last = tokens[-1]
			if last == '':
				if is_const:
					ret += '*const '
					is_const = False
				else:
					ret += '*mut '
			elif last == 'const':
				ret += '*const '
			else:
				ret += '*const '
				print(f"Unknown pointer modifier '{last}'")
			tokens.pop()
		argt = tokens[0]
		if argt.startswith('struct '):
			argt = argt[7:]
		if argt == "void":
			argt = "c_void"
		ret += argt
		return ret

	def rs_arg(args, fp = False, emit_argn = False, with_self = True):
		retarg = []
		if with_self: retarg += ['&self']
		for arg in args.split(','):
			try:
				argt, argn = arg.strip().rsplit(' ', 1)
				argn = rs_keyword_rename(argn)
				ret = rs_argtype_conv(argt)
				if fp:
					if not emit_argn:
						ret = f'/* {argn} */ ' + ret
				else:
					if emit_argn:
						ret = f'_: {ret}'
					else:
						ret = f'{argn}: {ret}'
				retarg += [ret]
			except ValueError:
				if arg == 'void':
					pass
				else:
					retarg += arg
		retarg = ', '.join(retarg)
		return retarg

	def rs_arg_fp(args):
		return rs_arg(args, fp = True, emit_argn = True, with_self = False)

	def rs_call_arg(args):
		retarg = []
		for arg in args.split(','):
			try:
				argt, argn = arg.strip().rsplit(' ', 1)
				argn = rs_keyword_rename(argn)
				retarg += [argn]
			except ValueError:
				if arg == 'void':
					retarg += ['']
				else:
					retarg += arg
		retarg = ', '.join(retarg)
		return retarg

	def rs_ret(rettype, use_result = True):
		ret = rs_argtype_conv(rettype)
		if use_result:
			if ret in {'void', 'c_void'}:
				return ' -> Result<()>'
			else:
				return f' -> Result<{ret}>'
		else:
			if ret in {'void', 'c_void'}:
				return ''
			else:
				return f' -> {ret}'


	def rs_const_value(number):
		if number.endswith('ull'):
			number = number[:-3] + 'u64'
		elif number.endswith('u'):
			number = number[:-1] + 'u32'
		return number

	def _overload_check(membername) -> tuple:
		preserve = ''
		dimension = ''
		typeabbr = ''
		is_v = False
		ovlname = ''
		matched = False
		for ovlpre in overloadables:
			if not membername.startswith(ovlpre): continue
			i = len(ovlpre)
			preserve = ''
			for pre in overload_preserve_prefix:
				if membername[i:i + len(pre)] == pre:
					preserve = pre
					i += len(pre)
					break
			dimension = ''
			for dim in mat_dims:
				if membername[i:i + len(dim)] == dim:
					dimension = dim
					i += len(dim)
					break
			typeabbr = ''
			for abr in type_abbrs:
				if membername[i:i + len(abr)] == abr:
					typeabbr = abr
					i += len(abr)
					break
			is_v = False
			if membername[-1] == 'v':
				is_v = True
			if membername.endswith('Pointer') and preserve == 'P':
				preserve = ''
			matched = bool(dimension or typeabbr)
			if matched:
				ovlname = ovlpre
				break
		return (matched, ovlname, preserve, dimension, typeabbr, is_v)

	def _style_change(ident):
		ident = ident.lower()
		for a in range(ord('a'), ord('z') + 1):
			a = chr(a)
			ident = ident.replace(f'_{a}', a.upper())
		ident = ident.replace('_', '')
		ident = f'{ident[0].upper()}{ident[1:]}'
		if ident.startswith('Esv'): ident = f'EsV{ident[len("Esv"):]}'
		return ident

	def _on_version(x):
		nonlocal versions, version_name
		version_name = x['id']
		versions[version_name] = {
			'typealias': {},
			'define': {},
			'functype': {},
			'funcproto': {},
			'type2proto': {}
		}

	def _on_typealias(x):
		nonlocal versions
		target_type = x['target_type']
		typealias = x['alias']
		global already_defined
		if typealias[0] in already_defined: return
		already_defined |= {typealias[0]}
		try:
			versions[version_name]['typealias'][target_type] += typealias
		except KeyError:
			versions[version_name]['typealias'][target_type] = typealias

	def _on_define(x):
		nonlocal versions
		defn = x['id']
		defv = x['value']
		global already_defined
		if defn in already_defined: return
		already_defined |= {defn}
		versions[version_name]['define'][defn] = defv

	def _on_functype(x):
		nonlocal versions
		typename = x['typename']
		global already_defined
		if typename in already_defined: return
		already_defined |= {typename}
		versions[version_name]['functype'][typename] = x

	def _on_funcproto(x):
		nonlocal versions
		funcname = x['funcname']
		versions[version_name]['funcproto'][funcname] = x
		versions[version_name]['type2proto'][f'PFN{funcname.upper()}PROC'] = funcname

	def _on_version_end(x):
		nonlocal OpenGL, version_name, firstver_name, firstver_classname, last_version, parsed, outs_hpp, outs_cpp, outs_csharp, outs_rs, csharp_typeconv, rs_traits
		curver = versions[version_name]
		class_name = _style_change(version_name)
		rs_trait_name = version_name.replace('VERSION', PREFIX)
		rs_traits += [rs_trait_name]
		rs_first_trait_name = None
		if firstver_name:
			rs_first_trait_name = firstver_name.replace('VERSION', PREFIX)
		func2load = {} # functions to be loaded
		overloads = {} # key: 'Xxxxx[1,2,3,4][N,I,P,L][s,f,i,d,ub,us,ui]'; value = (rettype, 'Xxxxx', arglist)
		type2proto = curver['type2proto']
		proto2type = {v: k for k, v in type2proto.items()}
		OpenGL = 'OpenGL'
		refver = 'gl4'
		if version_name.startswith('ES_'):
			version_name = f'ES{version_name[len("ES_"):]}'
			OpenGL = 'OpenGL ES'
			refver = 'es3.0'
		try:
			major, minor, release = version_name.split('_')[1:]
		except ValueError:
			major, minor, release = version_name.split('_')[1:] + ['0']
		is_first_ver = last_version is None
		is_first_es_ver = version_name == 'ESVERSION_2_0'

		outs_rs[class_name] = {
			'predef': io.StringIO(),
			'struct': io.StringIO(),
			'impl': io.StringIO(),
			'trait': io.StringIO(),
		}

		global_member = (version_name.lower(), class_name)
		outs_rs['global']['struct'].write(f'\t/// Functions from {OpenGL} version {major}.{minor}\n')
		outs_rs['global']['struct'].write(f'\tpub {global_member[0]}: {global_member[1]},\n')
		outs_rs['global']['struct'].write(f'\n')
		outs_rs['global']['impl'].write(f'impl {rs_trait_name}_g for {rs_global_struct_name} {{\n')
		outs_rs['global']['trait'].write(f'pub trait {rs_trait_name}_g {{\n')
		outs_rs['global']['members'] += [global_member]

		outs_rs[class_name]['struct'].write(f'\n')
		outs_rs[class_name]['struct'].write(f'/// Functions from {OpenGL} version {major}.{minor}\n')
		outs_rs[class_name]['struct'].write(f'{rust_derive}\n')
		outs_rs[class_name]['struct'].write(f'pub struct {class_name} {{\n')
		outs_rs[class_name]['impl'].write(f'impl {rs_trait_name} for {class_name} {{\n')
		outs_rs[class_name]['trait'].write('\n')
		outs_rs[class_name]['trait'].write(f'/// Functions from {OpenGL} version {major}.{minor}\n')
		outs_rs[class_name]['trait'].write(f'pub trait {rs_trait_name} {{\n')
		if not is_first_ver and version_name != 'ESVERSION_2_0':
			outs_rs[class_name]['trait'].write(f"\t/// Reference: <https://registry.khronos.org/OpenGL-Refpages/{refver}/html/glGetError.xhtml>\n")
			outs_rs[class_name]['trait'].write('\tfn glGetError(&self) -> GLenum;\n')

		for target_type, typealias in curver['typealias'].items():
			outs_hpp.write(f'\ttypedef {target_type} {", ".join(typealias)};\n')

			for alias in typealias:
				while alias[0] == '*':
					alias = alias[1:]
					target_type = target_type + "*"
				rs_target_type = rs_type_conv(rs_argtype_conv(target_type))

				outs_rs[class_name]['predef'].write('\n')
				outs_rs[class_name]['predef'].write(f'/// Alias to `{rs_target_type}`\n')
				outs_rs[class_name]['predef'].write(f'pub type {alias} = {rs_target_type};\n')
			try:
				target_of_target = csharp_typeconv[target_type]
			except KeyError:
				target_of_target = target_type
			for a in typealias:
				if a not in csharp_typeconv:
					csharp_typeconv[a] = target_of_target
		cst = csharp_typeconv

		csharp_func2load = {}
		csharp_olfuncs = {}
		def add_csharp_overload_functions(funcname, rettype, delename, csarglist, unsafe=False):
			nonlocal csharp_olfuncs
			funcdata = rettype, delename, csarglist, unsafe
			if funcname not in csharp_olfuncs:
				csharp_olfuncs[funcname] = [funcdata]
			elif funcdata not in csharp_olfuncs[funcname]:
				csharp_olfuncs[funcname] += [funcdata]

		outs_csharp.write(f'\t#region "{PREFIX_}{version_name}"\n')
		csharp_funcimp = io.StringIO()
		csharp_constdef = io.StringIO()
		csharp_deletype = io.StringIO()
		csharp_deledef = io.StringIO()
		csharp_delecb = io.StringIO()
		csharp_utilities = io.StringIO()
		csharp_ctor = io.StringIO()
		csharp_overloads = io.StringIO()

		# Convert to C# arglist
		def csargs(arglist, with_marshalas_tag=True, always_use_ref=False, always_use_list=False, always_use_intptr=False, keep_pointers=False):
			if [always_use_ref, always_use_list, always_use_intptr].count(True) > 1:
				raise ValueError(f'Invalid arguments')
			csarg = []
			havecount = False
			for arg in arglist.split(','):
				mod = ''
				cstype = ''
				haveconst = 'const' in arg
				arg = arg.replace('const ', ' ').replace('const*', '*').replace('* * ', '** ').replace('  ', ' ').strip()
				if arglist.strip() == 'void': return ""
				argtype, argname = arg.rsplit(' ', 1)
				if argname in csharp_keywords: argname += '_'
				if '*' in argtype:
					levels = argtype.count('*')
					basetype = argtype.split('*', 1)[0]
					if keep_pointers:
						cstype = cst[basetype] + '*' * levels
					elif always_use_intptr:
						cstype = 'IntPtr'
					elif levels == 1:
						if cst[basetype] == 'char':
							if with_marshalas_tag: mod = f'[MarshalAs(UnmanagedType.LPStr)] {mod}'
							if haveconst:
								cstype = 'string'
							else:
								cstype = 'StringBuilder'
						elif havecount and not always_use_ref or always_use_list:
							if cst[basetype] == 'void':
								cstype = 'IntPtr'
							else:
								if with_marshalas_tag: mod = f'[MarshalAs(UnmanagedType.LPArray)] {mod}'
								cstype = f'{cst[basetype]}[]'
						else:
							if cst[basetype] == 'void':
								cstype = 'IntPtr'
							else:
								mod = f'ref {mod}'
								cstype = cst[basetype]
					elif levels == 2:
						if cst[basetype] == 'char':
							if with_marshalas_tag: mod = f'[MarshalAs(UnmanagedType.LPArray)] {mod}'
							cstype = 'string[]'
						elif havecount and not always_use_ref or always_use_list:
							if with_marshalas_tag: mod = f'[MarshalAs(UnmanagedType.LPArray)] {mod}'
							if cst[basetype] == 'void':
								cstype = 'IntPtr[]'
							else:
								cstype = f'{cst[basetype]}[][]'
						else:
							mod = f'ref {mod}'
							if cst[basetype] == 'void':
								cstype = 'IntPtr'
							else:
								cstype = f'{cst[basetype]}[]'
					else:
						cstype = 'IntPtr'
				else:
					cstype = cst[argtype]
					if with_marshalas_tag:
						if cstype == 'bool':
							cstype = f'[MarshalAs(UnmanagedType.Bool)] {cstype}'
					if argname in {'count', 'n'}: havecount = True
					if argname.startswith('num'): havecount = True
				csarg += [(f'{mod}{cstype}', argname)]
			return ', '.join([f'{t} {n}' for t, n in csarg])
		def cscallarg(csarglist):
			return ", ".join([("ref " if 'ref' in ptype.split(' ') else "") + pname.strip() for ptype, pname in [param.rsplit(" ", 1) for param in csarglist.split(", ")]])
		# Convert to C# rettype
		def csret(rettype, keep_pointers=False):
			try:
				return cst[rettype]
			except KeyError:
				pass
			if '*' in rettype:
				if keep_pointers:
					try:
						return cst[rettype.split('*', 1)[0]] + '*' * rettype.count('*')
					except KeyError:
						pass
				else:
					return 'IntPtr';
			raise ValueError(f"Unknown {rettype}")

		for functype, fpdata in curver['functype'].items():
			if functype in type2proto: continue
			rettype = fpdata['ret']
			calltype = fpdata['calltype']
			arglist = fpdata['arglist']
			outs_hpp.write(f'\tusing {functype} = {rettype} ({calltype}) ({arglist});\n')
			outs_rs['global']['predef'].write('\n')
			outs_rs['global']['predef'].write(f'/// The prototype to the {OpenGL} callback function `{functype}`\n')
			outs_rs['global']['predef'].write(f'pub type {functype} = extern "system" fn({rs_arg_fp(arglist)}){rs_ret(rettype, use_result = False)};\n')
			csharp_delecb.write(f'\t\tpublic delegate {csret(rettype)} {functype} ({csargs(arglist)});\n')
		outs_hpp.write('\n')

		l_class_name = None
		if is_first_ver:
			firstver_name = version_name
			firstver_classname = class_name
			outs_hpp.write(f'\tclass {class_name}\n')
			outs_csharp.write(f'\tclass {class_name}\n')
			outs_csharp.write('\t{\n')
			#static_const_aliases = []
			for funcn, funcproto in curver['funcproto'].items():
				rettype = funcproto['ret']
				calltype = funcproto['calltype']
				arglist = funcproto['arglist']
				membername = funcn[len(prefix):]
				#static_const_aliases += [f'\tconst {class_name}::PFN{funcn.upper()}PROC {class_name}::{membername} = {funcn};']

				outs_cpp.write(f'\tGLAPI {rettype} {calltype} {funcn} ({arglist});\n')

				csrettype = csret(rettype)
				if '*' in arglist:
					singlename = f'{funcn}_ref'
					multiname = f'{funcn}_list'
					safename = f'{funcn}_safe'
					unmanname = f'{funcn}_unman'
					unsafename = f'{funcn}_unsafe'
					safeargs = csargs(arglist)
					singleargs = csargs(arglist, always_use_ref=True)
					multiargs = csargs(arglist, always_use_list=True)
					unmanargs = csargs(arglist, always_use_intptr=True)
					unsafeargs = csargs(arglist, keep_pointers=True)
					if singleargs != multiargs:
						csharp_funcimp.write(f'\t\t[DllImport("opengl32.dll", EntryPoint = "{funcn}")]\n')
						csharp_funcimp.write(f'\t\tpublic static extern {csrettype} {singlename} ({singleargs});\n')
						csharp_funcimp.write(f'\t\t[DllImport("opengl32.dll", EntryPoint = "{funcn}")]\n')
						csharp_funcimp.write(f'\t\tpublic static extern {csrettype} {multiname} ({multiargs});\n')
						csharp_funcimp.write(f'\t\t[DllImport("opengl32.dll", EntryPoint = "{funcn}")]\n')
						csharp_funcimp.write(f'\t\tpublic static extern {csrettype} {unmanname} ({unmanargs});\n')
						csharp_funcimp.write(f'\t\t[DllImport("opengl32.dll", EntryPoint = "{funcn}")]\n')
						csharp_funcimp.write(f'\t\tpublic static extern unsafe {csrettype} {unsafename} ({unsafeargs});\n')
					else:
						if safeargs != unmanargs:
							csharp_funcimp.write(f'\t\t[DllImport("opengl32.dll", EntryPoint = "{funcn}")]\n')
							csharp_funcimp.write(f'\t\tpublic static extern {csrettype} {unmanname} ({unmanargs});\n')
						csharp_funcimp.write(f'\t\t[DllImport("opengl32.dll", EntryPoint = "{funcn}")]\n')
						csharp_funcimp.write(f'\t\tpublic static extern {csrettype} {safename} ({safeargs});\n')
						csharp_funcimp.write(f'\t\t[DllImport("opengl32.dll", EntryPoint = "{funcn}")]\n')
						csharp_funcimp.write(f'\t\tpublic static extern unsafe {csrettype} {unsafename} ({unsafeargs});\n')
				else:
					csarglist = csargs(arglist)
					csharp_funcimp.write(f'\t\t[DllImport("opengl32.dll", EntryPoint = "{funcn}")]\n')
					csharp_funcimp.write(f'\t\tpublic static extern {csrettype} {funcn} ({csarglist});\n')
			#outs_cpp.write('\n'.join(static_const_aliases))
			outs_cpp.write('\n')
		else:
			l_class_name = _style_change(last_version)
			outs_hpp.write(f'\tclass {class_name} : public {l_class_name}\n')
			outs_csharp.write(f'\tclass {class_name} : {l_class_name}\n')
			outs_csharp.write('\t{\n')
		if not is_first_ver and not is_first_es_ver:
			outs_rs[class_name]['impl'].write(f"\t/// Reference: <https://registry.khronos.org/OpenGL-Refpages/{refver}/html/glGetError.xhtml>\n")
			outs_rs[class_name]['impl'].write("\t#[inline(always)]\n")
			outs_rs[class_name]['impl'].write(f"\tfn glGetError(&self) -> GLenum {{\n")
			outs_rs[class_name]['impl'].write(f'\t\t(self.geterror)()\n')
			outs_rs[class_name]['impl'].write('\t}\n')
		for funcn, funcproto in curver['funcproto'].items():
			rettype = funcproto['ret']
			calltype = funcproto['calltype']
			arglist = funcproto['arglist']
			membername = funcn[len(prefix):]
			functype = f'PFN{funcn.upper()}PROC'

			rs_ret_type = rs_ret(rettype, use_result = False)
			rs_call_from_class = f'(self.{membername.lower()})({rs_call_arg(arglist)})'
			rs_call_from_global = f'(self.{version_name.lower()}.{membername.lower()})({rs_call_arg(arglist)})'
			if "*const GLubyte" in rs_ret_type:
				rs_ret_type = " -> Result<&'static str>"
				rs_call_from_class = "unsafe{CStr::from_ptr(" + rs_call_from_class + " as *const i8)}.to_str().unwrap()"
				rs_call_from_global = "unsafe{CStr::from_ptr(" + rs_call_from_global + " as *const i8)}.to_str().unwrap()"
			elif membername == 'GetError':
				rs_ret_type = " -> GLenum"
			else:
				rs_ret_type = rs_ret(rettype, use_result = True)
			outs_rs[class_name]['trait'].write("\n")
			outs_rs[class_name]['trait'].write(f"\t/// Reference: <https://registry.khronos.org/OpenGL-Refpages/{refver}/html/{funcn}.xhtml>\n")
			outs_rs[class_name]['trait'].write(f"\tfn {funcn}({rs_arg(arglist)}){rs_ret_type};\n")
			if membername == 'GetError':
				outs_rs[class_name]['impl'].write("\t#[inline(always)]\n")
				outs_rs[class_name]['impl'].write(f"\tfn {funcn}({rs_arg(arglist)}){rs_ret_type} {{\n")
				outs_rs[class_name]['impl'].write(f'\t\t{rs_call_from_class}\n')
				outs_rs[class_name]['impl'].write('\t}\n')
			else:
				outs_rs[class_name]['impl'].write("\t#[inline(always)]\n")
				outs_rs[class_name]['impl'].write(f"\tfn {funcn}({rs_arg(arglist)}){rs_ret_type} {{\n")
				outs_rs[class_name]['impl'].write(f'\t\tlet ret = process_catch("{funcn}", catch_unwind(||{rs_call_from_class}));\n')
				outs_rs[class_name]['impl'].write(f'\t\t#[cfg(feature = "diagnose")]\n')
				outs_rs[class_name]['impl'].write(f'\t\tif let Ok(ret) = ret {{\n')
				outs_rs[class_name]['impl'].write(f'\t\t\treturn to_result("{funcn}", ret, self.glGetError());\n')
				outs_rs[class_name]['impl'].write('\t\t} else {\n')
				outs_rs[class_name]['impl'].write('\t\t\treturn ret\n')
				outs_rs[class_name]['impl'].write('\t\t}\n')
				outs_rs[class_name]['impl'].write(f'\t\t#[cfg(not(feature = "diagnose"))]\n')
				outs_rs[class_name]['impl'].write('\t\treturn ret;\n')
				outs_rs[class_name]['impl'].write('\t}\n')
		if is_first_ver:
			outs_rs[class_name]['impl'].write("\t#[inline(always)]\n")
			outs_rs[class_name]['impl'].write("\tfn get_version(&self) -> (&'static str, u32, u32, u32) {\n")
			outs_rs[class_name]['impl'].write("\t\t(self.spec, self.major_version, self.minor_version, self.release_version)\n")
			outs_rs[class_name]['impl'].write("\t}\n")
			outs_rs[class_name]['impl'].write("\t#[inline(always)]\n")
			outs_rs[class_name]['impl'].write("\tfn get_vendor(&self) -> &'static str {\n")
			outs_rs[class_name]['impl'].write("\t\tself.vendor\n")
			outs_rs[class_name]['impl'].write("\t}\n")
			outs_rs[class_name]['impl'].write("\t#[inline(always)]\n")
			outs_rs[class_name]['impl'].write("\tfn get_renderer(&self) -> &'static str {\n")
			outs_rs[class_name]['impl'].write("\t\tself.renderer\n")
			outs_rs[class_name]['impl'].write("\t}\n")
			outs_rs[class_name]['impl'].write("\t#[inline(always)]\n")
			outs_rs[class_name]['impl'].write("\tfn get_versionstr(&self) -> &'static str {\n")
			outs_rs[class_name]['impl'].write("\t\tself.version\n")
			outs_rs[class_name]['impl'].write("\t}\n")
			outs_rs['global']['impl'].write("\t#[inline(always)]\n")
			outs_rs['global']['impl'].write("\tfn get_version(&self) -> (&'static str, u32, u32, u32) {\n")
			outs_rs['global']['impl'].write(f"\t\tself.{firstver_name.lower()}.get_version()\n")
			outs_rs['global']['impl'].write("\t}\n")
			outs_rs['global']['impl'].write("\t#[inline(always)]\n")
			outs_rs['global']['impl'].write("\tfn get_vendor(&self) -> &'static str {\n")
			outs_rs['global']['impl'].write(f"\t\tself.{firstver_name.lower()}.get_vendor()\n")
			outs_rs['global']['impl'].write("\t}\n")
			outs_rs['global']['impl'].write("\t#[inline(always)]\n")
			outs_rs['global']['impl'].write("\tfn get_renderer(&self) -> &'static str {\n")
			outs_rs['global']['impl'].write(f"\t\tself.{firstver_name.lower()}.get_renderer()\n")
			outs_rs['global']['impl'].write("\t}\n")
			outs_rs['global']['impl'].write("\t#[inline(always)]\n")
			outs_rs['global']['impl'].write("\tfn get_versionstr(&self) -> &'static str {\n")
			outs_rs['global']['impl'].write(f"\t\tself.{firstver_name.lower()}.get_versionstr()\n")
			outs_rs['global']['impl'].write("\t}\n")
		elif 'SHADING_LANGUAGE_VERSION' in curver['define'].keys():
			outs_rs[class_name]['trait'].write("\tfn get_shading_language_version(&self) -> &'static str;\n")
			outs_rs[class_name]['impl'].write("\t#[inline(always)]\n")
			outs_rs[class_name]['impl'].write("\tfn get_shading_language_version(&self) -> &'static str {\n")
			outs_rs[class_name]['impl'].write("\t\tself.shading_language_version\n")
			outs_rs[class_name]['impl'].write("\t}\n")
			outs_rs['global']['trait'].write("\tfn get_shading_language_version(&self) -> &'static str;\n")
			outs_rs['global']['impl'].write("\t#[inline(always)]\n")
			outs_rs['global']['impl'].write("\tfn get_shading_language_version(&self) -> &'static str {\n")
			outs_rs['global']['impl'].write(f"\t\tself.{version_name.lower()}.shading_language_version\n")
			outs_rs['global']['impl'].write("\t}\n")
		outs_rs[class_name]['impl'].write("}\n\n")
		outs_rs[class_name]['impl'].write(f"impl {class_name} {{\n")
		if is_first_ver:
			outs_rs[class_name]['impl'].write("\tpub fn new(mut get_proc_address: impl FnMut(&'static str) -> *const c_void) -> Result<Self> {\n")
			outs_rs[class_name]['impl'].write("\t\tlet mut ret = Self {\n")
			outs_rs[class_name]['impl'].write("\t\t\tavailable: true,\n")
			outs_rs[class_name]['impl'].write('\t\t\tspec: "unknown",\n')
			outs_rs[class_name]['impl'].write("\t\t\tmajor_version: 0,\n")
			outs_rs[class_name]['impl'].write("\t\t\tminor_version: 0,\n")
			outs_rs[class_name]['impl'].write("\t\t\trelease_version: 0,\n")
			outs_rs[class_name]['impl'].write('\t\t\tvendor: "unknown",\n')
			outs_rs[class_name]['impl'].write('\t\t\trenderer: "unknown",\n')
			outs_rs[class_name]['impl'].write('\t\t\tversion: "unknown",\n')
			outs_rs[class_name]['trait'].write("\tfn get_version(&self) -> (&'static str, u32, u32, u32);\n")
			outs_rs[class_name]['trait'].write("\tfn get_vendor(&self) -> &'static str;\n")
			outs_rs[class_name]['trait'].write("\tfn get_renderer(&self) -> &'static str;\n")
			outs_rs[class_name]['trait'].write("\tfn get_versionstr(&self) -> &'static str;\n")
		else:
			l_class_name = _style_change(last_version)
			outs_rs[class_name]['impl'].write(f"\tpub fn new(base: impl {rs_first_trait_name}, mut get_proc_address: impl FnMut(&'static str) -> *const c_void) -> Self {{\n")
			outs_rs[class_name]['impl'].write("\t\tlet (_spec, major, minor, release) = base.get_version();\n")
			outs_rs[class_name]['impl'].write(f"\t\tif (major, minor, release) < ({major}, {minor}, {release}) {{\n")
			outs_rs[class_name]['impl'].write("\t\t\treturn Self::default();\n")
			outs_rs[class_name]['impl'].write("\t\t}\n")
			outs_rs[class_name]['impl'].write("\t\tSelf {\n")
			outs_rs[class_name]['impl'].write("\t\t\tavailable: true,\n")
		if not is_first_ver and not is_first_es_ver:
			outs_rs[class_name]['impl'].write('\t\t\tgeterror: {let proc = get_proc_address("glGetError"); if proc == null() {dummy_pfnglgeterrorproc} else {unsafe{transmute(proc)}}},\n')
		for funcn, funcproto in curver['funcproto'].items():
			membername = funcn[len(prefix):]
			functype = f'PFN{funcn.upper()}PROC'
			outs_rs[class_name]['impl'].write(f'\t\t\t{membername.lower()}: {{let proc = get_proc_address("{funcn}"); if proc == null() {{dummy_{functype.lower()}}} else {{unsafe{{transmute(proc)}}}}}},\n')
		if is_first_ver:
			outs_rs[class_name]['impl'].write('\t\t};\n')
			outs_rs[class_name]['impl'].write('\t\tret.fetch_version()?;\n')
			outs_rs[class_name]['impl'].write('\t\tOk(ret)\n')
		else:
			if 'SHADING_LANGUAGE_VERSION' in curver['define'].keys():
				outs_rs[class_name]['impl'].write('\t\t\tshading_language_version: base.glGetString(GL_SHADING_LANGUAGE_VERSION).unwrap(),\n')
			outs_rs[class_name]['impl'].write('\t\t}\n')
		outs_rs[class_name]['impl'].write('\t}\n')

		outs_hpp.write('\t{\n')
		if 'SHADING_LANGUAGE_VERSION' in curver['define'].keys():
			outs_hpp.write('\tpublic:\n')
			outs_hpp.write('\t\tinline std::string GetShadingLanguageVersion() { return ShadingLanguageVersion; }\n')
		outs_hpp.write('\tprotected:\n')
		for functype, fpdata in curver['functype'].items():
			if functype not in type2proto: continue
			rettype = fpdata['ret']
			calltype = fpdata['calltype']
			arglist = fpdata['arglist']
			pproto = type2proto[functype]
			proto = pproto[len(prefix):]
			membername = proto
			funcn = pproto
			outs_hpp.write(f'\t\tusing {functype} = {rettype} ({calltype}) ({arglist});\n')
			outs_cpp.write(f'\tstatic {rettype} {calltype[:-1]} Null_{pproto} ({arglist})')
			if rettype == 'void':
				outs_cpp.write('{ NullFuncPtr(); }\n')
			else:
				outs_cpp.write('{ NullFuncPtr(); return 0; }\n')
			outs_rs[class_name]['predef'].write('\n')
			outs_rs[class_name]['predef'].write(f'/// The prototype to the OpenGL function `{proto}`\n')
			outs_rs[class_name]['predef'].write(f'type {functype} = extern "system" fn({rs_arg_fp(arglist)}){rs_ret(rettype, use_result = False)};\n')
			args = [arg.strip() for arg in arglist.split(',')]
			#if proto.startswith('Gen') and proto.endswith('s') and len(args) == 2 and args[0].endswith((' n', ' count')) and args[1].count('*') == 1 and 'const' not in args[1] and rettype == 'void':
			if '*' in arglist:
				csrettype = csret(rettype)
				singlename = f'{proto}_ref'
				singletype = f'PFN{PREFIX}{singlename.upper()}PROC'
				multiname = f'{proto}_list'
				multitype = f'PFN{PREFIX}{multiname.upper()}PROC'
				unmanname = f'{proto}_unman'
				unmantype = f'PFN{PREFIX}{unmanname.upper()}PROC'
				unsafename = f'{proto}_unsafe'
				unsafetype = f'PFN{PREFIX}{unsafename.upper()}PROC'
				csarg_ref = csargs(arglist, always_use_ref=True)
				csarg_list = csargs(arglist, always_use_list=True)
				csarg_unman = csargs(arglist, always_use_intptr=True)
				csarg_unsafe = csargs(arglist, keep_pointers=True)
				if csarg_ref != csarg_list:
					csarg_o_ref = csargs(arglist, always_use_ref=True, with_marshalas_tag=False)
					csarg_o_list = csargs(arglist, always_use_list=True, with_marshalas_tag=False)
					csarg_o_unman = csargs(arglist, always_use_intptr=True, with_marshalas_tag=False)
					csarg_o_unsafe = csargs(arglist, keep_pointers=True, with_marshalas_tag=False)
					csharp_deletype.write(f'\t\tpublic delegate {csrettype} {singletype} ({csarg_ref});\n')
					csharp_deletype.write(f'\t\tpublic delegate {csrettype} {multitype} ({csarg_list});\n')
					csharp_deletype.write(f'\t\tpublic delegate {csrettype} {unmantype} ({csarg_unman});\n')
					csharp_deletype.write(f'\t\tpublic unsafe delegate {csrettype} {unsafetype} ({csarg_unsafe});\n')
					csharp_deledef.write(f'\t\tpublic readonly {singletype} {singlename};\n')
					csharp_deledef.write(f'\t\tpublic readonly {multitype} {multiname};\n')
					csharp_deledef.write(f'\t\tpublic readonly {unmantype} {unmanname};\n')
					csharp_deledef.write(f'\t\tpublic readonly {unsafetype} {unsafename};\n')
					add_csharp_overload_functions(proto, rettype, singlename, csarg_o_ref)
					add_csharp_overload_functions(proto, rettype, multiname, csarg_o_list)
					add_csharp_overload_functions(proto, rettype, unmanname, csarg_o_unman)
					add_csharp_overload_functions(proto, rettype, unsafename, csarg_o_unsafe, unsafe=True)
					if not is_first_ver:
						csharp_func2load[singlename] = singletype, pproto
						csharp_func2load[multiname] = multitype, pproto
						csharp_func2load[unmanname] = unmantype, pproto
						csharp_func2load[unsafename] = unsafetype, pproto
					else:
						csharp_func2load[singlename] = singletype, f'{pproto}_ref'
						csharp_func2load[multiname] = multitype, f'{pproto}_list'
						csharp_func2load[unmanname] = unmantype, f'{pproto}_unman'
						csharp_func2load[unsafename] = unsafetype, f'{pproto}_unsafe'
				else:
					csrettype = csret(rettype, keep_pointers=True)
					safename = f'{proto}_safe'
					safetype = f'PFN{PREFIX}{safename.upper()}PROC'
					unmanname = f'{proto}_unman'
					unmantype = f'PFN{PREFIX}{unmanname.upper()}PROC'
					unsafename = f'{proto}_unsafe'
					unsafetype = f'PFN{PREFIX}{unsafename.upper()}PROC'
					csarg_safe = csargs(arglist)
					csarg_unman = csargs(arglist, always_use_intptr=True)
					csarg_unsafe = csargs(arglist, keep_pointers=True)
					csarg_o_safe = csargs(arglist, with_marshalas_tag=False)
					csarg_o_unman = csargs(arglist, always_use_intptr=True, with_marshalas_tag=False)
					csarg_o_unsafe = csargs(arglist, keep_pointers=True, with_marshalas_tag=False)
					if csarg_safe != csarg_unman:
						csharp_deletype.write(f'\t\tpublic delegate {csrettype} {unmantype} ({csarg_unman});\n')
						csharp_deledef.write(f'\t\tpublic readonly {unmantype} {unmanname};\n')
						add_csharp_overload_functions(proto, rettype, unmanname, csarg_o_unman)
					csharp_deletype.write(f'\t\tpublic delegate {csrettype} {safetype} ({csarg_safe});\n')
					csharp_deletype.write(f'\t\tpublic unsafe delegate {csrettype} {unsafetype} ({csarg_unsafe});\n')
					csharp_deledef.write(f'\t\tpublic readonly {safetype} {safename};\n')
					csharp_deledef.write(f'\t\tpublic readonly {unsafetype} {unsafename};\n')
					add_csharp_overload_functions(proto, rettype, safename, csarg_o_safe)
					add_csharp_overload_functions(proto, rettype, unsafename, csarg_o_unsafe, unsafe=True)
					if not is_first_ver:
						if csarg_safe != csarg_unman:
							csharp_func2load[unmanname] = unmantype, pproto
						csharp_func2load[safename] = safetype, pproto
						csharp_func2load[unsafename] = unsafetype, pproto
					else:
						if csarg_safe != csarg_unman:
							csharp_func2load[unmanname] = unmantype, f'{pproto}_unman'
						csharp_func2load[safename] = safetype, f'{pproto}_safe'
						csharp_func2load[unsafename] = unsafetype, f'{pproto}_unsafe'
			else:
				csharp_deletype.write(f'\t\tpublic delegate {csret(rettype)} {functype} ({csargs(arglist)});\n')
				csharp_deledef.write(f'\t\tpublic readonly {functype} {proto};\n')
				csharp_func2load[proto] = functype, pproto
			rs_ret_type = rs_ret(rettype, use_result = False)
			rs_call_from_class = f'(self.{membername.lower()})({rs_call_arg(arglist)})'
			rs_call_from_global = f'(self.{version_name.lower()}.{membername.lower()})({rs_call_arg(arglist)})'
			if "*const GLubyte" in rs_ret_type:
				rs_ret_type = " -> Result<&'static str>"
				rs_call_from_class = "unsafe{CStr::from_ptr(" + rs_call_from_class + " as *const i8)}.to_str().unwrap()"
				rs_call_from_global = "unsafe{CStr::from_ptr(" + rs_call_from_global + " as *const i8)}.to_str().unwrap()"
			elif membername == 'GetError':
				rs_ret_type = " -> GLenum"
			else:
				rs_ret_type = rs_ret(rettype, use_result = True)
			outs_rs['global']['trait'].write("\n")
			outs_rs['global']['trait'].write(f"\t/// Reference: <https://registry.khronos.org/OpenGL-Refpages/{refver}/html/{funcn}.xhtml>\n")
			outs_rs['global']['trait'].write(f"\tfn {funcn}({rs_arg(arglist)}){rs_ret_type};\n")
			if funcn == 'glGetError':
				outs_rs['global']['impl'].write("\t#[inline(always)]\n")
				outs_rs['global']['impl'].write(f"\tfn {funcn}({rs_arg(arglist)}){rs_ret_type} {{\n")
				outs_rs['global']['impl'].write(f'\t\t{rs_call_from_global}\n')
				outs_rs['global']['impl'].write('\t}\n')
			else:
				outs_rs['global']['impl'].write("\t#[inline(always)]\n")
				outs_rs['global']['impl'].write(f"\tfn {funcn}({rs_arg(arglist)}){rs_ret_type} {{\n")
				outs_rs['global']['impl'].write(f'\t\tlet ret = process_catch("{funcn}", catch_unwind(||{rs_call_from_global}));\n')
				outs_rs['global']['impl'].write(f'\t\t#[cfg(feature = "diagnose")]\n')
				outs_rs['global']['impl'].write(f'\t\tif let Ok(ret) = ret {{\n')
				outs_rs['global']['impl'].write(f'\t\t\treturn to_result("{funcn}", ret, (self.{version_name.lower()}.geterror)());\n')
				outs_rs['global']['impl'].write('\t\t} else {\n')
				outs_rs['global']['impl'].write('\t\t\treturn ret\n')
				outs_rs['global']['impl'].write('\t\t}\n')
				outs_rs['global']['impl'].write(f'\t\t#[cfg(not(feature = "diagnose"))]\n')
				outs_rs['global']['impl'].write('\t\treturn ret;\n')
				outs_rs['global']['impl'].write('\t}\n')
		outs_rs['global']['trait'].write('}\n\n')
		outs_rs['global']['impl'].write('}\n\n')
		if is_first_ver:
			outs_hpp.write('\t\tFunc_GetProcAddress GetProcAddress;\n')
			outs_hpp.write('\t\tint Ver_Major;\n')
			outs_hpp.write('\t\tint Ver_Minor;\n')
			outs_hpp.write('\t\tint Ver_Release;\n')
			outs_hpp.write('\t\tstd::string Vendor;\n')
			outs_hpp.write('\t\tstd::string Renderer;\n')
			outs_hpp.write('\t\tstd::string Version;\n')
			csharp_utilities.write('\t\tpublic readonly Delegate_GetProcAddress GetProcAddress;\n')
			csharp_utilities.write('\t\tpublic readonly int Ver_Major;\n')
			csharp_utilities.write('\t\tpublic readonly int Ver_Minor;\n')
			csharp_utilities.write('\t\tpublic readonly int Ver_Release;\n')
			csharp_utilities.write('\t\tpublic readonly string Vendor;\n')
			csharp_utilities.write('\t\tpublic readonly string Renderer;\n')
			csharp_utilities.write('\t\tpublic readonly string Version;\n')
			csharp_utilities.write('\t\tpublic TDelegate GetOpenGLFunctionDelegate<TDelegate> (string ProcName)\n')
			csharp_utilities.write('\t\t{\n')
			csharp_utilities.write('\t\t\tvar FuncPtr = GetProcAddress(ProcName);\n')
			csharp_utilities.write('\t\t\tif (FuncPtr == IntPtr.Zero) throw new NullOpenGLFunctionPointerException(String.Format("Could not get OpenGL function `{0}`.", ProcName));\n')
			csharp_utilities.write('\t\t\treturn Marshal.GetDelegateForFunctionPointer<TDelegate>(FuncPtr);\n')
			csharp_utilities.write('\t\t}\n')
			outs_rs[class_name]['struct'].write("\tspec: &'static str,\n")
			outs_rs[class_name]['struct'].write('\tmajor_version: u32,\n')
			outs_rs[class_name]['struct'].write('\tminor_version: u32,\n')
			outs_rs[class_name]['struct'].write('\trelease_version: u32,\n')
			outs_rs[class_name]['struct'].write("\tvendor: &'static str,\n")
			outs_rs[class_name]['struct'].write("\trenderer: &'static str,\n")
			outs_rs[class_name]['struct'].write("\tversion: &'static str,\n")
			outs_rs[class_name]['impl'].write("\t#[inline(always)]\n")
			outs_rs[class_name]['impl'].write("\tfn fetch_version(&mut self) -> Result<()> {\n")
			outs_rs[class_name]['impl'].write('\t\tself.vendor = self.glGetString(GL_VENDOR)?;\n')
			outs_rs[class_name]['impl'].write('\t\tself.renderer = self.glGetString(GL_RENDERER)?;\n')
			outs_rs[class_name]['impl'].write('\t\tself.version = self.glGetString(GL_VERSION)?;\n')
			outs_rs[class_name]['impl'].write('\t\tself.spec = "OpenGL";\n')
			outs_rs[class_name]['impl'].write('\t\tlet mut verstr = self.version;\n')
			outs_rs[class_name]['impl'].write('\t\tif verstr.starts_with("OpenGL ES ") {\n')
			outs_rs[class_name]['impl'].write('\t\t\tverstr = &verstr["OpenGL ES ".len()..];\n')
			outs_rs[class_name]['impl'].write('\t\t\tself.spec = "OpenGL ES ";\n')
			outs_rs[class_name]['impl'].write("\t\t} else if let Some((left, right)) = verstr.split_once(' ') {\n")
			outs_rs[class_name]['impl'].write("\t\t\tverstr = left;\n")
			outs_rs[class_name]['impl'].write("\t\t\tself.spec = right;\n")
			outs_rs[class_name]['impl'].write("\t\t}\n")
			outs_rs[class_name]['impl'].write("\t\tlet mut v: Vec<&str> = verstr.split('.').collect();\n")
			outs_rs[class_name]['impl'].write('\t\tv.resize(3, "0");\n')
			outs_rs[class_name]['impl'].write('\t\tv = v.into_iter().map(|x|if x == "" {"0"} else {x}).collect();\n')
			outs_rs[class_name]['impl'].write('\t\tself.major_version = v[0].parse().unwrap();\n')
			outs_rs[class_name]['impl'].write('\t\tself.minor_version = v[1].parse().unwrap();\n')
			outs_rs[class_name]['impl'].write('\t\tself.release_version = v[2].parse().unwrap();\n')
			outs_rs[class_name]['impl'].write('\t\tOk(())\n')
			outs_rs[class_name]['impl'].write('\t}\n')
		elif 'SHADING_LANGUAGE_VERSION' in curver['define'].keys():
			outs_hpp.write('\t\tstd::string ShadingLanguageVersion;\n')
			outs_rs[class_name]['struct'].write(f'\t/// The version of the {OpenGL} shading language\n')
			outs_rs[class_name]['struct'].write("\tshading_language_version: &'static str,\n")
			outs_rs[class_name]['struct'].write('\n')
			csharp_utilities.write('\t\tpublic readonly string ShadingLanguageVersion;\n')
		outs_hpp.write('\n')
		outs_hpp.write('\tprivate:\n')
		outs_hpp.write('\t\tbool Available;\n')
		outs_hpp.write('\n')
		outs_hpp.write('\tpublic:\n')
		outs_rs[class_name]['struct'].write(f'\t/// Is {OpenGL} version {major}.{minor} available\n')
		outs_rs[class_name]['struct'].write("\tavailable: bool,\n")
		outs_rs[class_name]['struct'].write('\n')
		outs_rs[class_name]['impl'].write("\t#[inline(always)]\n")
		outs_rs[class_name]['impl'].write("\tpub fn get_available(&self) -> bool {\n")
		outs_rs[class_name]['impl'].write(f'\t\tself.available\n')
		outs_rs[class_name]['impl'].write('\t}\n')
		if is_first_ver:
			outs_hpp.write('\t\ttemplate<typename FuncType>\n')
			outs_hpp.write('\t\tFuncType GetProc(const char* symbol, FuncType DefaultBehaviorFunc)\n')
			outs_hpp.write('\t\t{\n')
			outs_hpp.write('\t\t\tvoid *ProcAddress = GetProcAddress(symbol);\n');
			outs_hpp.write('\t\t\tif (!ProcAddress)\n')
			outs_hpp.write('\t\t\t{\n')
			outs_hpp.write('\t\t\t\treturn DefaultBehaviorFunc;\n')
			outs_hpp.write('\t\t\t}\n')
			outs_hpp.write(f'\t\t\treturn {cppfunc_cast}<FuncType>(ProcAddress);\n')
			outs_hpp.write('\t\t}\n')
			outs_hpp.write('\t\tinline void GetVersion(int& Major, int& Minor, int& Release)\n')
			outs_hpp.write('\t\t{\n')
			outs_hpp.write('\t\t\tMajor = Ver_Major;\n')
			outs_hpp.write('\t\t\tMinor = Ver_Minor;\n')
			outs_hpp.write('\t\t\tRelease = Ver_Release;\n')
			outs_hpp.write('\t\t}\n')
			outs_hpp.write('\t\tinline std::string GetVendor() { return Vendor; }\n')
			outs_hpp.write('\t\tinline std::string GetRenderer() { return Renderer; }\n')
			outs_hpp.write('\t\tinline std::string GetVersion() { return Version; }\n')
		elif not is_first_es_ver:
			outs_rs[class_name]['struct'].write('\t/// The function pointer to `glGetError()`\n')
			outs_rs[class_name]['struct'].write("\tpub geterror: PFNGLGETERRORPROC,\n")

		csharp_utilities.write('\t\tprivate readonly bool Available;\n')

		outs_hpp.write(f'\t\t{class_name}() = delete;\n')
		outs_hpp.write(f'\t\t{class_name}(Func_GetProcAddress GetProcAddress);\n')

		outs_hpp.write(f'\t\tinline bool {class_name}IsAvailable() {{ return Available; }}\n')
		outs_hpp.write('\n')
		csharp_utilities.write(f'\t\tpublic bool {class_name}IsAvailable {{get => Available;}}\n')

		for functype, fpdata in curver['functype'].items():
			if functype not in type2proto: continue
			rettype = fpdata['ret']
			calltype = fpdata['calltype']
			arglist = fpdata['arglist']
			pproto = type2proto[functype]
			proto = pproto[len(prefix):]
			outs_rs[class_name]['predef'].write('\n')
			outs_rs[class_name]['predef'].write(f'/// The dummy function of `{proto}()`\n')
			outs_rs[class_name]['predef'].write(f'extern "system" fn dummy_{functype.lower()} ({rs_arg(arglist, emit_argn = True, with_self = False)}){rs_ret(rettype, use_result = False)} {{\n')
			outs_rs[class_name]['predef'].write(f'\tpanic!("{OpenGL} function pointer `{pproto}()` is null.")\n')
			outs_rs[class_name]['predef'].write('}\n')

		for defn, defv in curver['define'].items():
			if defv.startswith('0x'):
				if defv.endswith('ull'):
					deft = 'GLuint64'
				elif defv.endswith('ll'):
					deft = 'GLint64'
				elif defv.endswith(('u', 'ul')):
					deft = 'GLuint'
				else:
					deft = enumtype[f'{PREFIX_}{defn}']
			else:
				deft = enumtype[f'{PREFIX_}{defn}']
			outs_hpp.write(f'\t\tstatic constexpr {deft} {defn} = {defv};\n')
			outs_rs[class_name]['predef'].write(f'/// Constant value defined from {OpenGL} {major}.{minor}\n')
			outs_rs[class_name]['predef'].write(f"pub const GL_{defn}: {deft} = {rs_const_value(defv)};\n")
			outs_rs[class_name]['predef'].write('\n')
			if deft == 'GLuint64':
				csdefv = defv.replace('ull', 'ul')
			elif deft == 'GLint64':
				csdefv = defv.replace('ll', 'l')
			else:
				csdefv = defv.replace('ul', 'u')
			csharp_constdef.write(f'\t\tpublic readonly {cst[deft]} {defn} = {csdefv};\n')
		outs_hpp.write('\n')

		for funcn, funcproto in curver['funcproto'].items():
			rettype = funcproto['ret']
			calltype = funcproto['calltype']
			arglist = funcproto['arglist']
			functype = f'PFN{funcn.upper()}PROC'
			membername = funcn[len(prefix):]
			outs_hpp.write(f'\t\t{functype} {membername};\n')
			outs_rs[class_name]['struct'].write('\n')
			outs_rs[class_name]['struct'].write(f'\t/// The function pointer to `{funcn}()`\n')
			outs_rs[class_name]['struct'].write(f"\tpub {membername.lower()}: {functype},\n")

			func2load[membername] = funcn

			# Check overloadable functions
			matched, ovlname, preserve, dimension, typeabbr, is_v = _overload_check(membername)
			if matched:
				if is_v:
					ovlname = f'{ovlname}{preserve}{dimension}{typeabbr}v'
				else:
					ovlname = f'{ovlname}{preserve}{typeabbr}'
				if preserve != 'P' and dimension != '':
					if not is_v:
						overloads[membername] = (functype, rettype, ovlname, arglist)
					if '*' not in arglist:
						add_csharp_overload_functions(ovlname, rettype, membername, csargs(arglist, with_marshalas_tag=False))
		outs_hpp.write('\n')

		for membername, ovld in overloads.items():
			functype, rettype, ovlpre, arglist = ovld
			outs_hpp.write(f'\t\tinline {rettype} {ovlpre}({arglist}) const {{ ')
			if rettype != 'void': outs_hpp.write('return ')
			outs_hpp.write(f'{membername}({", ".join([pname.strip() for ptype, pname in [param.rsplit(" ", 1) for param in arglist.split(", ")]])});}}\n')

		for proto, funcinfos in csharp_olfuncs.items():
			for funcinfo in funcinfos:
				rettype, membername, csarglist, unsafe = funcinfo
				csharp_overloads.write(f'\t\tpublic {"unsafe " if unsafe else ""}{csret(rettype)} {proto}({csarglist}) {{ ')
				if rettype != 'void': csharp_overloads.write('return ')
				csharp_overloads.write(f'{membername}({cscallarg(csarglist)}); }}\n')

		outs_hpp.write('\t};\n')
		outs_rs[class_name]['struct'].write("}\n")

		if last_version:
			csharp_ctor.write(f'\t\tpublic {class_name}(Delegate_GetProcAddress GetProcAddress) : base(GetProcAddress)\n')
			outs_cpp.write(f'\t{class_name}::{class_name}(Func_GetProcAddress GetProcAddress):\n')
			outs_cpp.write(f'\t\t{l_class_name}(GetProcAddress)')
		elif len(func2load):
			csharp_ctor.write(f'\t\tpublic {class_name}(Delegate_GetProcAddress GetProcAddress)\n')
			outs_cpp.write(f'\t{class_name}::{class_name}(Func_GetProcAddress GetProcAddress):\n')
			outs_cpp.write('\t\tGetProcAddress(GetProcAddress),\n')
		csharp_ctor.write('\t\t{\n')
		
		if not last_version:
			#outs_cpp.write(",\n".join([f"\t\t{membername}({funcname})" for membername, funcname in func2load.items()] + ['\t\tVer_Major(0)', '\t\tVer_Minor(0)', '\t\tVer_Release(0)']))
			if len(func2load):
				outs_cpp.write(",\n".join([f'\t\t{membername}(GetProc<PFN{funcname.upper()}PROC>("{funcname}", Null_{funcname}))' for membername, funcname in func2load.items()]))
			outs_cpp.write(',\n')
			outs_cpp.write(",\n".join(['\t\tVer_Major(0)', '\t\tVer_Minor(0)', '\t\tVer_Release(0)']))
			outs_cpp.write('\n\t{\n')
			outs_cpp.write('\t\tAvailable = true;\n')
			outs_cpp.write('\t\tauto Ver = (const char*)GetString(VERSION);\n')
			outs_cpp.write('\t\tVendor = (const char*)GetString(VENDOR);\n')
			outs_cpp.write('\t\tRenderer = (const char*)GetString(RENDERER);\n')
			outs_cpp.write('\t\tVersion = Ver;\n')
			outs_cpp.write('\t\tif (Ver)\n')
			outs_cpp.write('\t\t{\n')
			outs_cpp.write('\t\t\tauto ch = Ver;\n')
			outs_cpp.write('\t\t\tif (strstr(ch, "OpenGL ES")) ch += sizeof "OpenGL ES";\n')
			outs_cpp.write('\t\t\tVer_Major = atoi(ch);\n')
			outs_cpp.write('\t\t\twhile (isdigit(*ch)) ch++;\n')
			outs_cpp.write("\t\t\tif (*ch == '.')\n")
			outs_cpp.write('\t\t\t{\n')
			outs_cpp.write('\t\t\t\tch++;\n')
			outs_cpp.write('\t\t\t\tVer_Minor = atoi(ch);\n')
			outs_cpp.write('\t\t\t\twhile (isdigit(*ch)) ch++;\n')
			outs_cpp.write("\t\t\t\tif (*ch == '.')\n")
			outs_cpp.write('\t\t\t\t{\n')
			outs_cpp.write('\t\t\t\t\tch++;\n')
			outs_cpp.write('\t\t\t\t\tVer_Release = atoi(ch);\n')
			outs_cpp.write('\t\t\t\t}\n')
			outs_cpp.write('\t\t\t}\n')
			outs_cpp.write('\t\t}\n')
			outs_cpp.write('\t}\n')
			csharp_ctor.write('\t\t\tthis.GetProcAddress = GetProcAddress;\n')
			csharp_ctor.write('\t\t\tAvailable = true;\n')
			safe_load = []
			unsafe_load = []
			for membername, type_and_name in csharp_func2load.items():
				functype, funcname = type_and_name
				if membername.endswith('_unsafe') and funcname.endswith('_unsafe'):
					unsafe_load += [(membername, funcname)]
				else:
					safe_load += [(membername, funcname)]
			for membername, funcname in safe_load:
				csharp_ctor.write(f'\t\t\t{membername} = {funcname};\n')
			if len(unsafe_load):
				csharp_ctor.write('\t\t\tunsafe\n')
				csharp_ctor.write('\t\t\t{\n')
				for membername, funcname in unsafe_load:
					csharp_ctor.write(f'\t\t\t\t{membername} = {funcname};\n')
				csharp_ctor.write('\t\t\t}\n')
			csharp_ctor.write('\t\t\tvar VersionString = Marshal.PtrToStringAnsi(GetString(VERSION));\n')
			csharp_ctor.write('\t\t\tVendor = Marshal.PtrToStringAnsi(GetString(VENDOR));\n')
			csharp_ctor.write('\t\t\tRenderer = Marshal.PtrToStringAnsi(GetString(RENDERER));\n')
			csharp_ctor.write('\t\t\tVersion = VersionString;\n')
			csharp_ctor.write('\t\t\tif (!string.IsNullOrWhiteSpace(VersionString))\n')
			csharp_ctor.write('\t\t\t{\n')
			csharp_ctor.write('\t\t\t\tstring[] VendorSplit = VersionString.Split();\n')
			csharp_ctor.write("\t\t\t\tstring[] VersionSplit = VendorSplit[0].Split('.');\n")
			csharp_ctor.write('\t\t\t\tVer_Major = Convert.ToInt32(VersionSplit[0]);\n')
			csharp_ctor.write('\t\t\t\tVer_Minor = Convert.ToInt32(VersionSplit[1]);\n')
			csharp_ctor.write('\t\t\t\tVer_Release = Convert.ToInt32(VersionSplit[2]);\n')
			csharp_ctor.write('\t\t\t}\n')
		else:
			if len(func2load):
				outs_cpp.write(',\n')
				outs_cpp.write(",\n".join([f'\t\t{membername}(GetProc<PFN{funcname.upper()}PROC>("{funcname}", Null_{funcname}))' for membername, funcname in func2load.items()]))
			outs_cpp.write('\n\t{\n')
			if version_name.startswith('VERSION_'):
				outs_cpp.write(f'\t\tAvailable = Ver_Major > {major} || (Ver_Major == {major} && (Ver_Minor > {minor} || (Ver_Minor == {minor} && Ver_Release >= {release})));\n')
				if 'SHADING_LANGUAGE_VERSION' in curver['define'].keys():
					outs_cpp.write(f'\t\tShadingLanguageVersion = reinterpret_cast<const char*>(GetString(SHADING_LANGUAGE_VERSION));\n')
			else:
				outs_cpp.write(f'\t\tAvailable = true;\n')
			outs_cpp.write('\t}\n')

			csharp_ctor.write(f'\t\t\tAvailable = Ver_Major > {major} || (Ver_Major == {major} && (Ver_Minor > {minor} || (Ver_Minor == {minor} && Ver_Release >= {release})));\n')
			csharp_ctor.write(f'\t\t\tif (Available)\n')
			csharp_ctor.write('\t\t\t{\n')
			csharp_ctor.write('\t\t\t\ttry\n')
			csharp_ctor.write('\t\t\t\t{\n')
			for membername, type_and_name in csharp_func2load.items():
				functype, funcname = type_and_name
				csharp_ctor.write(f'\t\t\t\t\t{membername} = GetOpenGLFunctionDelegate<{functype}>("{funcname}");\n')
			csharp_ctor.write('\t\t\t\t}\n')
			csharp_ctor.write('\t\t\t\tcatch (NullOpenGLFunctionPointerException)\n')
			csharp_ctor.write('\t\t\t\t{\n')
			csharp_ctor.write('\t\t\t\t\tAvailable = false;\n')
			csharp_ctor.write('\t\t\t\t}\n')
			if 'SHADING_LANGUAGE_VERSION' in curver['define'].keys():
				csharp_ctor.write('\t\t\t\tShadingLanguageVersion = Marshal.PtrToStringAnsi(GetString(SHADING_LANGUAGE_VERSION));\n')
			csharp_ctor.write('\t\t\t}\n')
		outs_cpp.write('\n')

		csharp_ctor.write('\t\t}\n')

		outs_cpp.write('\n')
		outs_rs[class_name]['impl'].write("}\n\n")
		outs_rs[class_name]['trait'].write("}\n")

		outs_rs[class_name]['impl'].write(f"impl Default for {class_name} {{\n")
		outs_rs[class_name]['impl'].write("\tfn default() -> Self {\n")

		if is_first_ver:
			outs_rs[class_name]['impl'].write("\t\tSelf {\n")
			outs_rs[class_name]['impl'].write("\t\t\tavailable: false,\n")
			outs_rs[class_name]['impl'].write('\t\t\tspec: "unknown",\n')
			outs_rs[class_name]['impl'].write("\t\t\tmajor_version: 0,\n")
			outs_rs[class_name]['impl'].write("\t\t\tminor_version: 0,\n")
			outs_rs[class_name]['impl'].write("\t\t\trelease_version: 0,\n")
			outs_rs[class_name]['impl'].write('\t\t\tvendor: "unknown",\n')
			outs_rs[class_name]['impl'].write('\t\t\trenderer: "unknown",\n')
			outs_rs[class_name]['impl'].write('\t\t\tversion: "unknown",\n')
		else:
			l_class_name = _style_change(last_version)
			outs_rs[class_name]['impl'].write("\t\tSelf {\n")
			outs_rs[class_name]['impl'].write("\t\t\tavailable: false,\n")
		if not is_first_ver and not is_first_es_ver:
			outs_rs[class_name]['impl'].write(f'\t\t\tgeterror: dummy_pfnglgeterrorproc,\n')
		for funcn, funcproto in curver['funcproto'].items():
			membername = funcn[len(prefix):]
			functype = f'PFN{funcn.upper()}PROC'
			outs_rs[class_name]['impl'].write(f'\t\t\t{membername.lower()}: dummy_{functype.lower()},\n')
		if 'SHADING_LANGUAGE_VERSION' in curver['define'].keys():
			outs_rs[class_name]['impl'].write('\t\t\tshading_language_version: "unknown",\n')

		outs_rs[class_name]['impl'].write('\t\t}\n')
		outs_rs[class_name]['impl'].write('\t}\n')
		outs_rs[class_name]['impl'].write("}\n")

		outs_rs[class_name]['impl'].write(f'impl Debug for {class_name} {{\n')
		outs_rs[class_name]['impl'].write("\tfn fmt(&self, f: &mut Formatter) -> fmt::Result {\n")
		outs_rs[class_name]['impl'].write('\t\tif self.available {\n')
		outs_rs[class_name]['impl'].write(f'\t\t\tf.debug_struct("{class_name}")\n')
		outs_rs[class_name]['impl'].write(f'\t\t\t.field("available", &self.available)\n')
		if is_first_ver:
			outs_rs[class_name]['impl'].write(f'\t\t\t.field("spec", &self.spec)\n')
			outs_rs[class_name]['impl'].write(f'\t\t\t.field("major_version", &self.major_version)\n')
			outs_rs[class_name]['impl'].write(f'\t\t\t.field("minor_version", &self.minor_version)\n')
			outs_rs[class_name]['impl'].write(f'\t\t\t.field("release_version", &self.release_version)\n')
			outs_rs[class_name]['impl'].write(f'\t\t\t.field("vendor", &self.vendor)\n')
			outs_rs[class_name]['impl'].write(f'\t\t\t.field("renderer", &self.renderer)\n')
			outs_rs[class_name]['impl'].write(f'\t\t\t.field("version", &self.version)\n')
		elif 'SHADING_LANGUAGE_VERSION' in curver['define'].keys():
			outs_rs[class_name]['impl'].write(f'\t\t\t.field("shading_language_version", &self.shading_language_version)\n')
		for funcn, funcproto in curver['funcproto'].items():
			membername = funcn[len(prefix):].lower()
			functype = f'PFN{funcn.upper()}PROC'
			dummyfunc = f'dummy_{functype.lower()}'
			outs_rs[class_name]['impl'].write(f'\t\t\t.field("{membername}", unsafe' + '{' + f'if transmute::<_, *const c_void>(self.{membername}) == ({dummyfunc} as *const c_void) ' + '{' + f'&null::<{functype}>()' + '} else {' + f'&self.{membername}' + '}})\n')
		outs_rs[class_name]['impl'].write(f'\t\t\t.finish()\n')
		outs_rs[class_name]['impl'].write('\t\t} else {\n')
		outs_rs[class_name]['impl'].write(f'\t\t\tf.debug_struct("{class_name}")\n')
		outs_rs[class_name]['impl'].write(f'\t\t\t.field("available", &self.available)\n')
		outs_rs[class_name]['impl'].write(f'\t\t\t.finish_non_exhaustive()\n')
		outs_rs[class_name]['impl'].write('\t\t}\n')
		outs_rs[class_name]['impl'].write('\t}\n')
		outs_rs[class_name]['impl'].write('}\n\n')

		def mergeinto(desc, data):
			nonlocal outs_csharp
			if len(data):
				outs_csharp.write(f'\t\t#region "{desc}"\n')
				outs_csharp.write(data)
				if data[-1] != '\n': outs_csharp.write('\n')
				outs_csharp.write(f'\t\t#endregion // {desc}\n')

		mergeinto('Constants', csharp_constdef.getvalue())
		mergeinto('Function import', csharp_funcimp.getvalue())
		mergeinto('Callback delegate functions', csharp_delecb.getvalue())
		mergeinto('Delegate function types', csharp_deletype.getvalue())
		mergeinto('Delegate functions', csharp_deledef.getvalue())
		mergeinto('Utilities', csharp_utilities.getvalue())
		mergeinto('Constructor', csharp_ctor.getvalue())
		mergeinto('Overload functions', csharp_overloads.getvalue())

		outs_csharp.write('\t}\n')
		outs_csharp.write(f'\t#endregion // {PREFIX_}{version_name}\n')

		parsed['typealias'] |= curver['typealias']
		parsed['define'] |= curver['define']
		parsed['functype'] |= curver['functype']
		last_version = version_name

	on_stomach = {
		'version': _on_version,
		'typealias': _on_typealias,
		'define': _on_define,
		'functype': _on_functype,
		'funcproto': _on_funcproto,
		'version_end': _on_version_end
	}

	for parsefile in parsefiles:
		for swallow in _chew(parsefile):
			on_stomach[swallow['type']](swallow)

	outs_rs['global']['struct'].write("}\n")

	rs_global_members = outs_rs['global']['members']
	first_member_name = rs_global_members[0][0]

	outs_rs['global']['impl'].write(f'impl {rs_global_struct_name} {{\n')
	outs_rs['global']['impl'].write("\tpub fn new(mut get_proc_address: impl FnMut(&'static str) -> *const c_void) -> Result<Self> {\n")
	outs_rs['global']['impl'].write(f'\t\tlet {first_member_name} = {firstver_classname}::new(&mut get_proc_address)?;\n')
	outs_rs['global']['impl'].write(f'\t\tif !{first_member_name}.available {{\n')
	outs_rs['global']['impl'].write(f'\t\t\treturn Ok(Self::default());\n')
	outs_rs['global']['impl'].write('\t\t}\n')
	outs_rs['global']['impl'].write('\t\tOk(Self {\n')
	outs_rs['global']['impl'].write(f'\t\t\t{first_member_name},\n')
	for i in range(1, len(rs_global_members)):
		name, type = rs_global_members[i]
		outs_rs['global']['impl'].write(f'\t\t\t{name}: {type}::new({first_member_name}, &mut get_proc_address),\n')
	outs_rs['global']['impl'].write('\t\t})\n')
	outs_rs['global']['impl'].write('\t}\n')
	outs_rs['global']['impl'].write('}\n\n')

	outs_rs['global']['impl'].write(f'impl Default for {rs_global_struct_name} {{\n')
	outs_rs['global']['impl'].write("\tfn default() -> Self {\n")
	outs_rs['global']['impl'].write('\t\tSelf {\n')
	for member in rs_global_members:
		name, type = member
		outs_rs['global']['impl'].write(f'\t\t\t{name}: {type}::default(),\n')
	outs_rs['global']['impl'].write('\t\t}\n')
	outs_rs['global']['impl'].write('\t}\n')
	outs_rs['global']['impl'].write('}\n\n')

	outs_hpp.write('};\n')
	outs_cpp.write('};\n')
	outs_csharp.write('};\n')
	rs_items = []
	for (key, ver) in outs_rs.items():
		if key != 'global':
			rs_items += [ver]
	rs_global = outs_rs['global']
	outs_rs = '\n'.join(
		[rs_global['predef'].getvalue()] +
		['\n'.join([
			ver['predef'].getvalue(),
			ver['trait'].getvalue(),
			ver['struct'].getvalue(),
			ver['impl'].getvalue()
		]) for ver in rs_items] +
		[
			rs_global['trait'].getvalue(),
			rs_global['struct'].getvalue(),
			rs_global['impl'].getvalue()
		]
	)

	return outs_hpp.getvalue(), outs_cpp.getvalue(), outs_csharp.getvalue(), outs_rs.replace('\n\n\n', '\n')

if __name__ == '__main__':
	glxml = do_parse_glxml('gl.xml')
	hpp, cpp, cs, rs = do_parse(['glcore.h', 'gles32.h'], glxml)
	with open(f'{modname}.hpp', 'wb') as f: f.write(hpp.encode('utf-8'))
	with open(f'{modname}.cpp', 'wb') as f: f.write(cpp.encode('utf-8'))
	with open(f'{modname}.cs', 'w') as f: f.write(cs)
	with open(f'{modname}.rs', 'w') as f: f.write(rs)
