"""
.. module:: myriad_metaclass
    :platform: Linux
    :synposis: Provides metaclass for automatic Myriad integration

.. moduleauthor:: Pedro Rittner <pr273@cornell.edu>

"""
import inspect
import logging

from collections import OrderedDict
from copy import copy
from functools import wraps
from warnings import warn
from pkg_resources import resource_string

from pycparser.c_ast import ID, TypeDecl, Struct, PtrDecl, Decl

from myriad_mako_wrapper import MakoTemplate, MakoFileTemplate

from myriad_utils import OrderedSet

from myriad_types import MyriadScalar, MyriadFunction, MyriadStructType
from myriad_types import _MyriadBase, MyriadCType, MyriadTimeseriesVector
from myriad_types import MDouble, MVoid, MVarArgs, MSizeT, MInt

from ast_function_assembler import pyfun_to_cfun

#######
# Log #
#######

LOG = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())

#############
# Constants #
#############

# Default include headers for all C files
DEFAULT_LIB_INCLUDES = {"stdlib.h",
                        "stdio.h",
                        "assert.h",
                        "string.h",
                        "stddef.h",
                        "stdarg.h",
                        "stdint.h"}

# Default include headers for CUDA files
DEFAULT_CUDA_INCLUDES = {"cuda_runtime.h", "cuda_runtime_api.h"}


#############
# Templates #
#############

DELG_TEMPLATE = resource_string(
    __name__,
    "templates/delegator_func.mako").decode("UTF-8")

SUPER_DELG_TEMPLATE = resource_string(
    __name__,
    "templates/super_delegator_func.mako").decode("UTF-8")

CTOR_TEMPLATE_TEMPLATE = resource_string(
    __name__,
    "templates/ctor_template.mako").decode("UTF-8")

CLS_CTOR_TEMPLATE = resource_string(
    __name__,
    "templates/class_ctor_template.mako").decode("UTF-8")

CLS_CUDAFY_TEMPLATE = resource_string(
    __name__,
    "templates/class_cudafy_template.mako").decode("UTF-8")

INIT_OB_FUN_TEMPLATE = resource_string(
    __name__,
    "templates/init_ob_fun.mako").decode("UTF-8")

HEADER_FILE_TEMPLATE = resource_string(
    __name__,
    "templates/header_file.mako").decode("UTF-8")

CUH_FILE_TEMPLATE = resource_string(
    __name__,
    "templates/cuda_header_file.mako").decode("UTF-8")

C_FILE_TEMPLATE = resource_string(
    __name__,
    "templates/c_file.mako").decode("UTF-8")

PYC_COMP_FILE_TEMPLATE = resource_string(
    __name__,
    "templates/pyc_file.mako").decode("UTF-8")

######################
# Delegator Creation #
######################


def create_delegator(instance_fxn: MyriadFunction,
                     classname: str) -> MyriadFunction:
    """
    Creates a delegator function based on a function definition.

    :param MyriadFunction instance_fxn: Instance function to be wrapped
    :param str classname: Name of the class delegator
    :return: New MyriadFunction representing the delegator around instance_fxn
    :rtype: MyriadFunction
    """
    # Create copy with modified identifier
    ist_cpy = MyriadFunction.from_myriad_func(instance_fxn)
    # Generate template and render into copy's definition
    template_vars = {"delegator": ist_cpy, "classname": classname}
    template = MakoTemplate(DELG_TEMPLATE, template_vars)
    LOG.debug("Rendering create_delegator template for %s", classname)
    template.render()
    ist_cpy.fun_def = template.buffer
    # Return created copy
    return ist_cpy


def create_super_delegator(delg_fxn: MyriadFunction,
                           classname: str) -> MyriadFunction:
    """
    Create super delegator function.

    :param MyriadFunction delg_fxn: Delegator to create super_* wrapper for
    :param str classname: Name of the base class for this super delegator

    :return: Super delegator method as a MyriadFunction
    :rtype: MyriadFunction
    """
    # Create copy of delegator function with modified parameters
    super_args = copy(delg_fxn.args_list)
    super_class_arg = MyriadScalar("_class", MVoid, True, ["const"])
    tmp_arg_indx = len(super_args) + 1
    super_args[tmp_arg_indx] = super_class_arg
    super_args.move_to_end(tmp_arg_indx, last=False)
    s_delg_f = MyriadFunction.from_myriad_func(delg_fxn,
                                               "super_" + delg_fxn.ident,
                                               super_args)
    # Generate template and render
    template_vars = {"delegator": delg_fxn,
                     "super_delegator": s_delg_f,
                     "classname": classname}
    template = MakoTemplate(SUPER_DELG_TEMPLATE, template_vars)
    LOG.debug("Rendering create_super_delegator template for %s", classname)
    template.render()
    # Add rendered definition to function
    s_delg_f.fun_def = template.buffer
    return s_delg_f


def gen_instance_method_from_str(delegator, m_name: str,
                                 method_body: str) -> MyriadFunction:
    """
    Automatically generate a MyriadFunction wrapper for a method body.

    :param str m_name: Name to prepend to the instance method identifier
    :param str method_body: String template to use as the method body

    :return: Instance method as a MyriadFunction
    :rtype: MyriadFunction
    """
    return MyriadFunction(m_name + '_' + delegator.ident,
                          args_list=delegator.args_list,
                          ret_var=delegator.ret_var,
                          storage=['static'],
                          fun_def=method_body)

#####################
# Method Decorators #
#####################


def myriad_method(method):
    """
    Tags a method in a class to be a myriad method (i.e. converted to a C func)
    NOTE: This MUST be the first decorator applied to the function! E.g.:

        @another_decorator
        @yet_another_decorator
        @myriad_method
        def my_fn(stuff):
            pass

    This is because decorators replace the wrapped function's signature.
    """
    @wraps(method)
    def inner(*args, **kwargs):
        """ Dummy inner function to prevent direct method calls """
        raise Exception("Cannot directly call a myriad method")
    LOG.debug("myriad_method annotation wrapping %s", method.__name__)
    setattr(inner, "is_myriad_method", True)
    setattr(inner, "original_fun", method)
    return inner


def myriad_method_verbatim(method):
    """
    Tags a method in a class to be a myriad method (i.e. converted to a C func)
    but takes the docstring as verbatim C code.

    NOTE: This MUST be the first decorator applied to the function! E.g.:

        @another_decorator
        @yet_another_decorator
        @myriad_method_verbatim
        def my_fn(stuff):
            pass

    This is because decorators replace the wrapped function's signature.
    """
    @wraps(method)
    def inner(*args, **kwargs):
        """ Dummy inner function to prevent direct method calls """
        raise Exception("Cannot directly call a myriad method")
    setattr(inner, "is_myriad_method_verbatim", True)
    setattr(inner, "is_myriad_method", True)
    setattr(inner, "original_fun", method)
    return inner


def _myriadclass_method(method):
    """
    Tags a method in a class to be a MyriadClass method.

    MyriadClass methods are methods exclusive to MyriadClass; they are not
    declared as part of the MyriadClass struct but are used internally in
    MyriadObject.c to define behaviour tied to MyriadObject inheritance.

    NOTE: This MUST be the first decorator applied to the function! E.g.:

        @another_decorator
        @yet_another_decorator
        @_myriadclass_method
        def my_fn(stuff):
            pass

    This is because decorators replace the wrapped function's signature.
    """
    @wraps(method)
    def inner(*args, **kwargs):
        """ Dummy inner function to prevent direct method calls """
        raise Exception("Cannot directly call a myriad method")
    LOG.debug("myriad_method annotation wrapping %s", method.__name__)
    setattr(inner, "is_myriad_method_verbatim", True)
    setattr(inner, "is_myriad_method", True)
    setattr(inner, "is_myriadclass_method", True)
    setattr(inner, "original_fun", method)
    return inner

#####################
# MetaClass Wrapper #
#####################


class _MyriadObjectBase(object):
    """ Dummy placeholder class used for type checking, circular dependency"""

    @classmethod
    def _fill_in_base_methods(cls,
                              child_namespace: OrderedDict,
                              myriad_methods: OrderedDict):
        """
        Fills in missing base methods (e.g. ctor/etc) in child's namespace
        """
        # raise NotImplementedError("Not implemented in _MyriadObjectBase")
        pass


def _method_organizer_helper(supercls: _MyriadObjectBase,
                             myriad_methods: OrderedDict,
                             myriad_cls_vars: OrderedDict) -> OrderedSet:
    """
    Organizes Myriad Methods, including inheritance and verbatim methods.

    Verbatim methods are converted differently than pythonic methods; their
    docstring is embedded 'verbatim' into the template instead of going through
    the full AST conversion (though the function header is still processed).

    Returns an OrderedSet of methods not defined in the superclass
    """
    # Convert methods; remember, items() returns a read-only view
    for m_ident, method in myriad_methods.items():
        # Process verbatim methods
        verbatim = hasattr(method, "is_myriad_method_verbatim")
        # Check if verbatim methods have a docstring to use
        if verbatim and (method.__doc__ is None or method.__doc__ == ""):
            raise Exception("Verbatim method cannot have empty docstring")
        # Parse method, converting the body only if not verbatim
        myriad_methods[m_ident] = pyfun_to_cfun(method.original_fun, verbatim)
        # TODO: Use local var to avoid adding to own_methods (instead of attr)
        if hasattr(method, "is_myriadclass_method"):
            setattr(myriad_methods[m_ident], "is_myriadclass_method", True)

    # The important thing here is to decide which methods
    # (1) WE'VE CREATED, and
    # (2) Which methods are being OVERRRIDEN BY US that ORIGINATED ELSEWHERE
    def get_parent_methods(cls: _MyriadObjectBase) -> OrderedSet:
        """ Gets the own_methods of the parent class, and its parents, etc. """
        # If we're MyriadObject, we don't have any parent methods
        if cls is _MyriadObjectBase:
            return OrderedSet()
        else:
            return cls.own_methods.union(get_parent_methods(cls.__bases__[0]))

    parent_methods = get_parent_methods(supercls)
    LOG.debug("_method_organizer_helper parent methods: %r", parent_methods)

    # 'Own methods' are methods we've created (1); everything else is (2)
    own_methods = OrderedSet()
    for mtd in myriad_methods.values():
        if mtd in parent_methods or hasattr(mtd, "is_myriadclass_method"):
            continue
        # For methods we've created, generate class variables for class struct
        own_methods.add(mtd)
        new_ident = "my_" + mtd.fun_typedef.name
        m_scal = MyriadScalar(new_ident, mtd.base_type)
        myriad_cls_vars[new_ident] = m_scal

    LOG.debug("_method_organizer_helper class variables selected: %r",
              myriad_cls_vars)

    return own_methods


def _template_creator_helper(namespace: OrderedDict) -> OrderedDict:
    """
    Creates templates using namespace, and returns the updated namespace.
    """
    namespace["c_file_template"] = MakoFileTemplate(
        namespace["obj_name"] + ".c",
        C_FILE_TEMPLATE,
        namespace)
    namespace["header_file_template"] = MakoFileTemplate(
        namespace["obj_name"] + ".h",
        HEADER_FILE_TEMPLATE,
        namespace)
    namespace["cuh_file_template"] = MakoFileTemplate(
        namespace["obj_name"] + ".cuh",
        CUH_FILE_TEMPLATE,
        namespace)
    # TODO: CU file template
    namespace["pyc_file_template"] = MakoFileTemplate(
        "py_" + namespace["obj_name"] + ".c",
        PYC_COMP_FILE_TEMPLATE,
        namespace)
    return namespace


def _generate_includes_helper(superclass, features: set=None) -> (set, set):
    """ Generates local and lib includes based on superclass and features """
    lcl_inc = [] if superclass is _MyriadObjectBase else [superclass.__name__]
    # TODO: Better detection of system/library headers
    lib_inc = copy(DEFAULT_LIB_INCLUDES)
    # TODO: Add CUDA includes on-demand
    return (lcl_inc, lib_inc)


def _parse_namespace(namespace: dict,
                     name: str,
                     myriad_methods: OrderedDict,
                     myriad_obj_vars: OrderedDict):
    """
    Parses the given namespace, updates the last three input arguments to have:
        1) OrderedDict of myriad_methods
        2) OrderedDict of myriad_obj_vars
        3) OrderedSet of verbatim methods
    """
    # Extracts variables and myriad methods from class definition
    for k, val in namespace.items():
        # if val is ...
        # ... a registered myriad method
        if hasattr(val, "is_myriad_method"):
            if hasattr(val, "is_myriadclass_method"):
                LOG.debug("%s is a MyriadClass method in %s", k, name)
            elif hasattr(val, "is_myriad_method_verbatim"):
                LOG.debug("%s is a verbatim myriad method in %s", k, name)
            else:
                LOG.debug("%s is a myriad method in %s", k, name)
            myriad_methods[k] = val
        # ... some generic non-Myriad function or method
        elif inspect.isfunction(val) or inspect.ismethod(val):
            LOG.debug("%s is a function or method, ignoring for %s", k, name)
        # ... some generic instance of a _MyriadBase type
        elif issubclass(val.__class__, _MyriadBase):
            LOG.debug("%s is a Myriad-type non-function attribute", k)
            myriad_obj_vars[k] = val
            LOG.debug("%s was added as an object variable to %s", k, name)
        # ... a type statement of base type MyriadCType (e.g. MDouble)
        elif issubclass(val.__class__, MyriadCType):
            myriad_obj_vars[k] = MyriadScalar(k, val)
            LOG.debug("%s has decl %s", k, myriad_obj_vars[k].stringify_decl())
            LOG.debug("%s was added as an object variable to %s", k, name)
        # ... a timeseries variable
        elif val is MyriadTimeseriesVector:
            # TODO: Enable different precisions for MyriadTimeseries
            myriad_obj_vars[k] = MyriadScalar(k, MDouble, arr_id="SIMUL_LEN")
        # ... a python meta value (e.g.  __module__) we shouldn't mess with
        elif k.startswith("__"):
            LOG.debug("Built-in method %r ignored for %s", k, name)
        # TODO: Figure out other valid values for namespace variables
        else:
            LOG.info("Unsupported var type for %r, ignoring in %s", k, name)
    LOG.debug("myriad_obj_vars for %s: %s ", name, myriad_obj_vars)


def _init_module_vars(obj_name: str,
                      cls_name: str,
                      is_myriad_obj: bool) -> OrderedDict:
    """ Special method for initializing MyriadObject objects"""
    module_vars = OrderedDict()
    if is_myriad_obj:
        # TODO: Replace this string with MyriadObject_obj_arr.mako template
        module_vars['object'] = """
static struct MyriadClass object[] =
{
    {
        { object + 1 },
        object,
        NULL,
        sizeof(struct MyriadObject),
        MyriadObject_ctor,
        MyriadObject_dtor,
        MyriadObject_cudafy,
        MyriadObject_decudafy,
    },
    {
        { object + 1 },
        object,
        NULL,
        sizeof(struct MyriadClass),
        MyriadClass_ctor,
        MyriadClass_dtor,
        MyriadClass_cudafy,
        MyriadClass_decudafy,
    }
};
        """
    module_vars[obj_name] =\
        MyriadScalar(
            obj_name,
            MVoid,
            True,
            quals=["const"],
            init=ID("object") if is_myriad_obj else None)
    module_vars[cls_name] =\
        MyriadScalar(
            cls_name,
            MVoid,
            True,
            quals=["const"],
            init=ID("object + 1") if is_myriad_obj else None)
    return module_vars


# TODO: Replace this with a Mako template
MYRIAD_OBJ_INIT_FUN = """
#ifdef CUDA
const struct MyriadClass *obj_addr = NULL, *class_addr = NULL;
const size_t obj_size = sizeof(struct MyriadObject);
const size_t class_size = sizeof(struct MyriadClass);

CUDA_CHECK_RETURN(cudaMalloc((void**)&obj_addr, class_size));
CUDA_CHECK_RETURN(cudaMalloc((void**)&class_addr, class_size));

const struct MyriadClass anon_class_class = {
    {class_addr},
    obj_addr,
    class_addr,
    class_size,
    NULL,
    NULL,
    NULL,
    NULL,
};

CUDA_CHECK_RETURN(
    cudaMemcpy(
        (void**) class_addr,
        &anon_class_class,
        sizeof(struct MyriadClass),
        cudaMemcpyHostToDevice
        )
    );

object[1].device_class = class_addr;

const struct MyriadClass anon_obj_class = {
    {class_addr},
    obj_addr,
    class_addr,
    obj_size,
    NULL,
    NULL,
    NULL,
    NULL,
};

CUDA_CHECK_RETURN(
    cudaMemcpy(
        (void**) obj_addr,
        &anon_obj_class,
        sizeof(struct MyriadClass),
        cudaMemcpyHostToDevice
        )
    );

object[0].device_class = (const struct MyriadClass*) obj_addr;

CUDA_CHECK_RETURN(
    cudaMemcpyToSymbol(
        (const void*) &MyriadClass_dev_t,
        &class_addr,
        sizeof(void*),
        0,
        cudaMemcpyHostToDevice
        )
    );

CUDA_CHECK_RETURN(
    cudaMemcpyToSymbol(
        (const void*) &MyriadObject_dev_t,
        &obj_addr,
        sizeof(void*),
        0,
        cudaMemcpyHostToDevice
        )
    );

return 0;
#else
return -1;
#endif
"""


def _initialize_obj_cls_structs(supercls: _MyriadObjectBase,
                                myriad_obj_vars: OrderedDict,
                                myriad_cls_vars: OrderedDict):
    """ Initializes object and class structs """
    if supercls is not _MyriadObjectBase:
        myriad_obj_vars["_"] = supercls.obj_struct("_", quals=["const"])
        myriad_cls_vars["_"] = supercls.cls_struct("_", quals=["const"])
    else:
        # Setup MyriadObject struct variables
        myriad_obj_vars["mclass"] = _gen_mclass_ptr_scalar("mclass")
        # Setup MyriadObjectClass struct variables
        tmp = MyriadScalar("_", MVoid, quals=["const"])
        tmp.type_decl = TypeDecl(declname="_", quals=[],
                                 type=Struct("MyriadObject", None))
        tmp.decl = Decl(name="_",
                        quals=["const"], storage=[],
                        funcspec=[], type=tmp.type_decl,
                        init=None, bitsize=None)
        myriad_cls_vars["_"] = tmp
        myriad_cls_vars["super"] = _gen_mclass_ptr_scalar("super")
        myriad_cls_vars["device_class"] =\
            _gen_mclass_ptr_scalar("device_class")
        myriad_cls_vars["size"] = MyriadScalar("size", MSizeT)


def _gen_mclass_ptr_scalar(ident: str):
    """ Quick and dirty way of hard-coding MyriadClass struct pointers """
    tmp = MyriadScalar(ident,
                       MVoid,
                       True,
                       quals=["const"])
    tmp.type_decl = TypeDecl(declname=ident,
                             quals=[],
                             type=Struct("MyriadClass", None))
    tmp.ptr_decl = PtrDecl(quals=[],
                           type=tmp.type_decl)
    tmp.decl = Decl(name=ident,
                    quals=["const"],
                    storage=[],
                    funcspec=[],
                    type=tmp.ptr_decl,
                    init=None,
                    bitsize=None)
    return tmp


def _gen_init_fun(namespace: OrderedDict, supercls: _MyriadObjectBase) -> str:
    """ Generates the init* function for modules """
    if supercls is _MyriadObjectBase:
        return MYRIAD_OBJ_INIT_FUN
    # Make temporary dictionary since we need to add an extra value
    tmp_dict = {"super_obj": supercls.obj_name, "super_cls": supercls.cls_name}
    tmp_dict.update(namespace)
    template = MakoTemplate(INIT_OB_FUN_TEMPLATE, tmp_dict)
    LOG.debug("Rendering init function for %s", namespace["obj_name"])
    template.render()
    return template.buffer


class MyriadMetaclass(type):
    """
    TODO: Documentation for MyriadMetaclass
    """

    @classmethod
    def __prepare__(mcs, name, bases):
        """
        Force the class to use an OrderedDict as its __dict__, for purposes of
        enforcing strict ordering of keys (necessary for making structs).
        """
        return OrderedDict()

    @staticmethod
    def myriad_init(self, **kwargs):
        # TODO: Check if all kwargs (including parents) are set
        for argname, argval in kwargs.items():
            self.__setattr__(argname, argval)

    @staticmethod
    def myriad_set_attr(self, **kwargs):
        """
        Prevent users from accessing objects except through py_x interfaces
        """
        raise NotImplementedError("Cannot set object attributes (yet).")

    def __new__(mcs, name, bases, namespace, **kwds):
        if len(bases) > 1:
            raise NotImplementedError("Multiple inheritance is not supported.")

        supercls = bases[0]  # Alias for base class
        if not issubclass(supercls, _MyriadObjectBase):
            raise TypeError("Myriad modules must inherit from MyriadObject")

        # Setup object/class variables, methods, and verbatim methods
        myriad_cls_vars = OrderedDict()
        myriad_obj_vars = OrderedDict()
        myriad_methods = OrderedDict()

        # Setup object with implicit superclass to start of struct definition
        _initialize_obj_cls_structs(supercls, myriad_obj_vars, myriad_cls_vars)

        # Parse namespace into appropriate variables
        _parse_namespace(namespace,
                         name,
                         myriad_methods,
                         myriad_obj_vars)

        # Object Name and Class Name are automatically derived from name
        namespace["obj_name"] = name
        namespace["cls_name"] = name + "Class"

        # Struct definition representing object state
        namespace["obj_struct"] = MyriadStructType(namespace["obj_name"],
                                                   myriad_obj_vars)

        # Organize myriad methods and class struct members
        namespace["own_methods"] = _method_organizer_helper(supercls,
                                                            myriad_methods,
                                                            myriad_cls_vars)

        # Add #include's from system libraries, local files, and CUDA headers
        namespace["local_includes"], namespace["lib_includes"] =\
            _generate_includes_helper(supercls)

        # Create myriad class struct
        namespace["cls_struct"] = MyriadStructType(namespace["cls_name"],
                                                   myriad_cls_vars)

        # Add other objects to namespace
        namespace["myriad_methods"] = myriad_methods
        namespace["myriad_obj_vars"] = myriad_obj_vars
        namespace["myriad_cls_vars"] = myriad_cls_vars

        # Fill in missing methods (ctor/etc.)
        supercls._fill_in_base_methods(namespace, myriad_methods)

        # Initialize module variables
        namespace["myriad_module_vars"] =\
            _init_module_vars(
                namespace["obj_name"],
                namespace["cls_name"],
                supercls is _MyriadObjectBase)

        # Initialize module functions
        namespace["init_fun"] = _gen_init_fun(namespace, supercls)

        # Write templates now that we have full information
        LOG.debug("Creating templates for class %s", name)
        namespace = _template_creator_helper(namespace)

        # Finally, delete function from namespace
        for method_id in myriad_methods.keys():
            if method_id in namespace:
                del namespace[method_id]

        # Generate internal module representation
        namespace["__init__"] = MyriadMetaclass.myriad_init
        namespace["__setattr__"] = MyriadMetaclass.myriad_set_attr
        return type.__new__(mcs, name, (supercls,), dict(namespace))


# TODO: MyriadObject definition
class MyriadObject(_MyriadObjectBase, metaclass=MyriadMetaclass):
    """ Base class that every myriad object inherits from """

    @myriad_method_verbatim
    def ctor(self,
             app: MyriadScalar("app", MVarArgs, ptr=True)
             ) -> MyriadScalar('', MVoid, ptr=True):
        """    return self;"""

    @myriad_method_verbatim
    def dtor(self) -> MInt:
        """
    _my_free(_self);
    return 0;
        """

    @myriad_method_verbatim
    def cudafy(self, clobber: MInt) -> MyriadScalar('', MVoid, ptr=True):
        """
    #ifdef CUDA
    struct MyriadObject* _self = (struct MyriadObject*) self;
    void* n_dev_obj = NULL;
    size_t my_size = myriad_size_of(self);

    const struct MyriadClass* tmp = _self->m_class;
    _self->m_class = _self->m_class->device_class;

    CUDA_CHECK_RETURN(cudaMalloc(&n_dev_obj, my_size));

    CUDA_CHECK_RETURN(
        cudaMemcpy(
            n_dev_obj,
            _self,
            my_size,
            cudaMemcpyHostToDevice
            )
        );

    _self->m_class = tmp;

    return n_dev_obj;
    #else
    return NULL;
    #endif
        """

    @myriad_method_verbatim
    def decudafy(self, cuda_self: MyriadScalar("cuda_self", MVoid, ptr=True)):
        """    return;"""

    @_myriadclass_method
    def cls_ctor(self,
                 app: MyriadScalar("app", MVarArgs, ptr=True)
                 ) -> MyriadScalar('', MVoid, ptr=True):
        """
    struct MyriadClass* _self = (struct MyriadClass*) self;
    const size_t offset = offsetof(struct MyriadClass, my_ctor);

    _self->super = va_arg(*app, struct MyriadClass*);
    _self->size = va_arg(*app, size_t);

    assert(_self->super);

    memcpy((char*) _self + offset,
           (char*) _self->super + offset,
           myriad_size_of(_self->super) - offset);

    va_list ap;
    va_copy(ap, *app);

    voidf selector = NULL; selector = va_arg(ap, voidf);

    while (selector)
    {
        const voidf curr_method = va_arg(ap, voidf);
        if (selector == (voidf) myriad_ctor)
        {
            *(voidf *) &_self->my_ctor = curr_method;
        } else if (selector == (voidf) myriad_cudafy) {
            *(voidf *) &_self->my_cudafy = curr_method;
        } else if (selector == (voidf) myriad_dtor) {
            *(voidf *) &_self->my_dtor = curr_method;
        } else if (selector == (voidf) myriad_decudafy) {
            *(voidf *) &_self->my_decudafy = curr_method;
        }
        selector = va_arg(ap, voidf);
    }
    return _self;
        """

    @_myriadclass_method
    def cls_dtor(self) -> MInt:
        """
    fprintf(stderr, "Destroying a Class is undefined behavior.");
    return -1;
        """

    @_myriadclass_method
    def cls_cudafy(self, clobber: MInt) -> MyriadScalar('', MVoid, ptr=True):
        """
    /*
     * Invariants/Expectations:
     *
     * A) The class we're given (_self) is fully initialized on the CPU
     * B) _self->device_class == NULL, will receive this fxn's result
     * C) _self->super has been set with (void*) SuperClass->device_class
     *
     * The problem here is that we're currently ignoring anything the
     * extended class passes up at us through super_, and so we're only
     * copying the c_class struct, not the rest of the class. To solve this,
     * what we need to do is to:
     *
     * 1) Memcopy the ENTIRETY of the old class onto a new heap pointer
     *     - This works because the extended class has already made any
     *       and all of their pointers/functions CUDA-compatible.
     * 2) Alter the "top-part" of the copied-class to go to CUDA
     *     - cudaMalloc the future location of the class on the device
     *     - Set our internal object's class pointer to that location
     * 3) Copy our copied-class to the device
     * 3a) Free our copied-class
     * 4) Return the device pointer to whoever called us
     *
     * Q: How do we keep track of on-device super class?
     * A: We take it on good faith that the under class has set their supercls
     *    to be the visible SuperClass->device_class.
     */
    #ifdef CUDA
    struct MyriadClass* _self = (struct MyriadClass*) self;

    const struct MyriadClass* dev_class = NULL;

    // DO NOT USE sizeof(struct MyriadClass)!
    const size_t class_size = myriad_size_of(_self);

    // Allocate space for new class on the card
    CUDA_CHECK_RETURN(cudaMalloc((void**)&dev_class, class_size));

    // Memcpy the entirety of the old class onto a new CPU heap pointer
    const struct MyriadClass* class_cpy =
        (const struct MyriadClass*) calloc(1, class_size);
    memcpy((void*)class_cpy, self, class_size);

    // Embedded object's class set to our GPU class; this ignores $clobber
    memcpy((void*)&class_cpy->_.m_class, &dev_class, sizeof(void*));

    CUDA_CHECK_RETURN(
        cudaMemcpy(
            (void*)dev_class,
            class_cpy,
            class_size,
            cudaMemcpyHostToDevice
            )
        );

    free((void*)class_cpy); // Can safely free since underclasses get nothing

    return (void*) dev_class;
    #else
    return NULL;
    #endif
        """

    @_myriadclass_method
    def cls_decudafy(self,
                     cuda_self: MyriadScalar("cuda_self", MVoid, ptr=True)):
        """
    fputs("De-CUDAfying a class is undefined behavior. Aborted. ", stderr);
    return;
        """

    @classmethod
    def render_templates(cls):
        """ Render internal templates to files"""
        # Render templates for the superclass
        if cls is not MyriadObject:
            cls.__bases__[0].render_templates()
        # Render templates for the current class
        LOG.debug("Rendering H File for %s", cls.__name__)
        getattr(cls, "header_file_template").render_to_file()
        LOG.debug("Rendering C File for %s", cls.__name__)
        getattr(cls, "c_file_template").render_to_file()
        LOG.debug("Rendering CUH File for %s", cls.__name__)
        getattr(cls, "cuh_file_template").render_to_file()
        # TODO: MyriadObject has no PYC file - yet
        if cls is not MyriadObject:
            LOG.debug("Rendering PYC File for %s", cls.__name__)
            getattr(cls, "pyc_file_template").render_to_file()

    @classmethod
    def _fill_in_base_methods(cls,
                              child_namespace: OrderedDict,
                              myriad_methods: OrderedDict):
        """
        Fills in missing base methods (e.g. ctor/etc) in child's namespace.

        # TODO: Consider whether dtor/cudafy/etc. should be filled in
        """
        # Fill in ctor if it's missing
        if "ctor" not in myriad_methods:
            template = MakoTemplate(CTOR_TEMPLATE_TEMPLATE, child_namespace)
            LOG.debug("Rendering ctor template for %s",
                      child_namespace["obj_name"])
            template.render()
            myriad_methods["ctor"] = MyriadFunction.from_myriad_func(
                getattr(cls, "myriad_methods")["ctor"],
                fun_def=template.buffer)
        if "cls_cudafy" not in myriad_methods:
            template = MakoTemplate(CLS_CUDAFY_TEMPLATE, child_namespace)
            LOG.debug("Rendering cls_cudafy template for %s",
                      child_namespace["obj_name"])
            template.render()
            myriad_methods["cls_cudafy"] = MyriadFunction.from_myriad_func(
                getattr(cls, "myriad_methods")["cls_cudafy"],
                fun_def=template.buffer)
        if "cls_ctor" not in myriad_methods:
            template = MakoTemplate(CLS_CTOR_TEMPLATE, child_namespace)
            LOG.debug("Rendering cls_ctor template for %s",
                      child_namespace["obj_name"])
            template.render()
            myriad_methods["cls_ctor"] = MyriadFunction.from_myriad_func(
                getattr(cls, "myriad_methods")["cls_ctor"],
                fun_def=template.buffer)

if __name__ == "__main__":
    LOG.addHandler(logging.StreamHandler())
    MyriadObject.render_templates()