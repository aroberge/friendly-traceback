"""core.py

The exception hook at the heart of Friendly.

You should not need to use any of the functions defined here;
they are considered to be internal functions, subject to change at any
time. If functions defined in friendly.__init__.py do not meet your needs,
please file an issue.
"""
import inspect
import os
import re
import traceback

from itertools import dropwhile

from . import info_generic
from . import info_specific
from . import info_variables
from . import debug_helper
from . import source_cache

from .my_gettext import current_lang

from .path_info import is_excluded_file, EXCLUDED_FILE_PATH, path_utils
from .runtime_errors import name_error
from .source_cache import cache, highlight_source
from .syntax_errors import analyze_syntax
from .syntax_errors import indentation_error
from .syntax_errors import source_info
from . import token_utils

try:
    import executing  # noqa
except ImportError:
    pass  # ignore errors when processed by Sphinx


STR_FAILED = "<exception str() failed>"  # Same as Python


def convert_value_to_message(value):
    """This converts the 'value' of an exception into a string, while
    being safe to use for custom exceptions which have been incorrectly
    defined. See issue #181 for an example.
    """
    _ = current_lang.translate
    try:
        message = str(value)
    except Exception:  # noqa
        message = STR_FAILED
    return message


class TracebackData:
    """Raw traceback info obtained from Python.

    Instances of this class are intended to include all the relevant
    information about an exception so that a FriendlyTraceback object
    can be created.
    """

    def __init__(self, etype, value, tb):
        """This object is initialized with the standard values for a
        traceback::

            etype, value, tb = sys.exc_info()
        """
        cache.remove("<fstring>")
        self.exception_type = etype
        self.exception_name = etype.__name__
        self.value = value
        self.message = convert_value_to_message(value)
        self.formatted_tb = traceback.format_exception(etype, value, tb)
        self.records = self.get_records(tb)

        # The following three attributes get their correct values in get_source_info()
        self.bad_line = "\n"
        self.filename = ""
        self.exception_frame = None
        self.get_source_info(etype, value)

        # The following four attributes get their correct values in locate_error()
        self.node = None
        self.node_text = ""
        self.node_range = None
        self.original_bad_line = ""

        if issubclass(etype, SyntaxError):
            self.statement = source_info.Statement(self.value, self.bad_line)
        else:
            self.statement = None
            self.locate_error(tb)

    def get_records(self, tb):
        """Get the traceback frame history, excluding those originating
        from our own code that are included either at the beginning or
        at the end of the traceback.
        """
        records = inspect.getinnerframes(tb, cache.context)
        records = list(
            dropwhile(lambda record: is_excluded_file(record.filename), records)
        )
        records.reverse()
        records = list(
            dropwhile(lambda record: is_excluded_file(record.filename), records)
        )
        records.reverse()
        # If all the records are removed, it means that all the error
        # is in our own code - or that of the user who chose to exclude
        # some files. If so, we make sure to have something to analyze
        # and help identify the problem.
        if not records and not issubclass(self.exception_type, SyntaxError):
            return inspect.getinnerframes(tb, cache.context)
        return records

    def get_source_info(self, etype, value):
        """Retrieves the file name and the line of code where the exception
        was raised.
        """
        if issubclass(etype, SyntaxError):
            self.filename = value.filename
            if value.text is not None:
                self.bad_line = value.text  # typically includes "\n"
                return

            # this can happen with editors_helpers.check_syntax()
            try:
                self.bad_line = cache.get_source_lines(value.filename)[value.lineno - 1]
            except Exception:  # noqa
                self.bad_line = "\n"
            return

        if self.records:
            self.exception_frame, self.filename, linenumber, _, _, _ = self.records[-1]
            _, line = cache.get_formatted_partial_source(
                self.filename, linenumber, None
            )
            self.bad_line = line.rstrip()
            return

        # We should never reach this stage.
        debug_helper.log("Internal error in TracebackData.get_source_info.")
        debug_helper.log("No records found.")
        debug_helper.log("etype:" + str(etype))
        debug_helper.log("value:" + str(value))
        debug_helper.log_error()

    def locate_error(self, tb):
        """Attempts to narrow down the location of the error."""
        if not self.records:
            return

        # 1) If we can find the precise location (node) on a line of code
        #    causing the exception, we note this location
        #    so that we can indicate it later with ^^^^^, something like:
        #
        #    20:     b = tuple(range(50))
        #    21:     try:
        # -->22:         print(a[50], b[0])
        #                      ^^^^^
        #    23:     except Exception as e:
        #
        # If the node spans the entire line, we do not bother to indicate
        # its specific location.
        #
        # 2) Sometimes, a node will span multiple lines. For example,
        #    line 22 shown above might have been written as:
        #
        #    print(a[
        #            50], b[0])
        #
        # If that is the case, we rewrite the node as a single line.
        #
        # Other than for NameError, we replace our definition (text) of
        # the line that caused the exception by that of the node itself,
        # to be used later for processing.
        if self.exception_name == "NameError":
            # `executing` cannot give us the node location in this case
            return self.locate_name_error()

        while True:
            if tb.tb_frame == self.exception_frame:
                break
            tb = tb.tb_next
            if not tb:
                return

        try:
            ex = executing.Source.executing(tb)
            self.node = ex.node
            self.node_text = ex.text()
            # We want to transform logical line (or parts thereof) into
            # something that fits on a single physical line.
            # \n could be a valid newline token or a character within
            # a string; we only want to replace newline tokens.
            if "\n" in self.node_text:
                tokens = token_utils.tokenize(self.node_text)
                tokens = [tok for tok in tokens if tok != "\n"]
                self.node_text = "".join(tok.string for tok in tokens)
            _bad_line = token_utils.strip_comment(self.bad_line)
            if (
                self.node_text
                and self.node_text in self.bad_line
                and self.node_text.strip() != _bad_line.strip()
            ):
                begin = self.bad_line.find(self.node_text)
                end = begin + len(self.node_text)
                self.node_range = begin, end
            if self.node_text.strip():
                self.original_bad_line = self.bad_line
                self.bad_line = self.node_text
        except Exception as e:
            debug_helper.log("Exception raised in TracebackData.use_executing.")
            debug_helper.log(str(e))

    def locate_name_error(self):
        """Finds the location of an unknown name"""
        name, _ignore = name_error.get_unknown_name(self.message)

        if name is not None and name in self.bad_line:
            begin = self.bad_line.find(name)
            end = begin + len(name)
            self.node_range = begin, end


# ====================
# The following is an example of a formatted traceback, with
# some parts identified with (partial) names used below
#
# Python exception:  [header]

# [message]
#     UnboundLocalError: local variable 'a' referenced before assignment

# [generic]
# In Python, variables that are used inside a function are known as
# local variables. Before they are used, they must be assigned a value.
# A variable that is used before it is assigned a value is assumed to
# be defined outside that function; it is known as a 'global'
# (or sometimes 'nonlocal') variable. You cannot assign a value to such
# a global variable inside a function without first indicating to
# Python that this is a global variable, otherwise you will see
# an UnboundLocalError.

# [cause_header and cause]
# Likely cause:
#     The variable that appears to cause the problem is 'a'.
#     Try inserting the statement
#         global a
#     as the first line inside your function.
#

# [last_call_ ...]
# Execution stopped on line 14 of file 'C:\Users...\test_unbound_local_error.py'.
#    12:
#    13:     try:
# -->14:         inner()
#
# inner: <function test_unbound_local_error.<location... >

# [exception_raised_ ...]
# Exception raised on line 11 of file 'C:\Users\...\test_unbound_local_error.py'.
#     9:     def inner():
#    10:         b = 2
# -->11:         a = a + b
#
# [6]
# b: 2


class FriendlyTraceback:
    """Main class for creating a friendly traceback.

    All the information available to the end user is stored in a
    dict called "info". The various keys of that dict are documented
    in the docstrings of the relevant methods.

    To get all possible attributes set up, one needs to call
    compile_info() after initializing this class. This is done so
    as to allow third-party users to selectively call only one of

    * assign_cause()
    * assign_generic()
    * assign_location()

    if they so wish.

    Various functions are available when using a friendly console,
    which can show part of the information compile here.
    Among those:

    * where() shows the result of assign_location()
    * why() shows the cause, as compiled by assign_cause()
    * hint() is a shorter version of why(), sometimes available.
    * what() shows the information compiled by assign_generic()
    """

    def __init__(self, etype, value, tb):
        """The basic argument are those generated after a traceback
        and obtained via::

            etype, value, tb = sys.exc_info()

        The "header" key for the info dict is assigned here."""
        _ = current_lang.translate
        try:
            self.tb_data = TracebackData(etype, value, tb)
        except Exception as e:
            debug_helper.log("Uncaught exception in TracebackData.")
            debug_helper.log_error(e)
            print("Internal problem in Friendly.")
            print("Please report this issue.")
            raise SystemExit
        self.info = {"header": _("Python exception:")}
        self.message = self.assign_message()  # language independent
        self.assign_tracebacks()

    def assign_message(self):
        """Assigns the error message, as the attribute ``message``
        which is something like::

            NameError: name 'a' is not defined
        """
        exc_name = self.tb_data.exception_name

        value = self.tb_data.value
        if hasattr(value, "msg"):
            self.info["message"] = f"{exc_name}: {value.msg}\n"
        else:
            message = convert_value_to_message(value)
            self.info["message"] = f"{exc_name}: {message}\n"
        return self.info["message"]

    def compile_info(self):
        """Compile all info that was not set in __init__."""
        self.assign_generic()
        self.assign_location()
        self.assign_cause()
        # removing null values
        to_remove = [key for key in self.info if not self.info[key]]
        for key in to_remove:
            del self.info[key]

    def recompile_info(self):
        """This is useful if we need to redisplay some information in a
        different language than what was originally used.
        """
        _ = current_lang.translate
        self.info["header"] = _("Python exception:")
        self.assign_tracebacks()
        self.compile_info()

    def assign_cause(self):
        """Determine the cause of an exception, which is what is returned
        by ``why()``.
        """
        _ = current_lang.translate
        if self.tb_data.filename in ["<unknown>", "<string>"]:
            return

        if STR_FAILED in self.message:
            self.info["cause"] = _(
                "Warning: improperly formed exception.\n"
                "I suspect that a custom exception has been raised\n"
                "with a non-string value used as a message.\n"
                "This can occur if a `__repr__` or a `__str__` method\n"
                "raises an exception or does not return a string.\n"
            )
        elif issubclass(self.tb_data.exception_type, SyntaxError):
            self.set_cause_syntax()
        else:
            self.set_cause_runtime()

    def set_cause_runtime(self):
        """For exceptions other than SyntaxError and subclasses.
        Sets the value of the following attributes:

        * cause_header
        * cause

        and possibly:

        * suggest

        the latter being the "hint" appended to the friendly traceback.
        """
        _ = current_lang.translate
        etype = self.tb_data.exception_type
        value = self.tb_data.value
        if self.tb_data.filename == "<stdin>":
            self.info["cause"] = cannot_analyze_stdin()
            return

        cause = info_specific.get_likely_cause(
            etype, value, self.tb_data.exception_frame, self.tb_data
        )  # [3]
        self.info.update(**cause)

    def set_cause_syntax(self):
        """For SyntaxError and subclasses. Sets the value of the following
        attributes:

        * cause_header
        * cause

        and possibly:

        * suggest

        the latter being the "hint" appended to the friendly traceback.
        """
        _ = current_lang.translate
        etype = self.tb_data.exception_type
        value = self.tb_data.value

        if self.tb_data.filename == "<unknown>":
            return

        if self.tb_data.filename == "<stdin>":
            self.info["cause"] = cannot_analyze_stdin()
            return

        if etype.__name__ == "IndentationError":
            self.info["cause"] = indentation_error.set_cause_indentation_error(
                value, self.tb_data.statement
            )
            return

        if etype.__name__ == "TabError":
            return

        cause = analyze_syntax.set_cause_syntax(value, self.tb_data)
        self.info.update(**cause)

    def assign_generic(self):
        """Assigns the generic information about a given error. This is
        the answer to ``what()`` as in "What is a NameError?"

        Sets the value of the following attribute:

        * generic
        """
        self.info["generic"] = info_generic.get_generic_explanation(
            self.tb_data.exception_name
        )

    def assign_location(self):
        """This sets the values of the answers to 'where()', that is
        the information about the location of the exception.

        To determine which attributes will be set, consult the docstring
        of the following methods.

        For SyntaxError and subclasses: self.locate_parsing_error()

        For other types of exceptions, self.locate_exception_raised(),
        and possibly self.locate_last_call().
        """
        if issubclass(self.tb_data.exception_type, SyntaxError):
            self.locate_parsing_error()
            return

        records = self.tb_data.records
        if not records:
            debug_helper.log("No record in assign_location().")
            return

        self.locate_exception_raised(records[-1])
        if len(records) > 1:
            self.locate_last_call(records[0])

    def locate_exception_raised(self, record):
        """Sets the values of the following attributes which are
        part of a friendly

        * exception_raised_header
        * exception_raised_source
        * exception_raised_variables
        """
        from .config import session

        _ = current_lang.translate

        frame, filename, linenumber, _func, lines, index = record
        if lines == ["\n"] and source_cache.idle_get_lines is not None:
            # skipcq: PYL-E1102
            lines = source_cache.idle_get_lines(filename, linenumber - 1)

        partial_source = get_partial_source(
            filename, linenumber, lines, index, self.tb_data.node_range
        )
        filename = path_utils.shorten_path(filename)

        unavailable = filename in ["<unknown>", "<string>"]
        if unavailable:
            self.info["exception_raised_source"] = _(
                "{filename} is not a regular Python file whose contents can be analyzed.\n"
            ).format(filename=filename)

        if session.use_rich:
            filename = f"`'{filename}'`"
        self.info["exception_raised_header"] = _(
            "Exception raised on line {linenumber} of file {filename}.\n"
        ).format(linenumber=linenumber, filename=filename)

        if unavailable:
            return

        self.info["exception_raised_source"] = partial_source["source"]

        if self.tb_data.node_text:
            line = self.tb_data.node_text
        else:
            line = partial_source["line"]

        var_info = info_variables.get_var_info(line, frame)
        if var_info:
            self.info["exception_raised_variables"] = var_info

    def locate_last_call(self, record):
        """Sets the values of the following attributes:

        * last_call_header
        * exception_raised_source
        * last_call_variables
        """
        from .config import session

        _ = current_lang.translate

        frame, filename, linenumber, _func, lines, index = record
        if lines == ["\n"] and source_cache.idle_get_lines is not None:
            # skipcq: PYL-E1102
            lines = source_cache.idle_get_lines(filename, linenumber - 1)
        partial_source = get_partial_source(filename, linenumber, lines, index)
        filename = path_utils.shorten_path(filename)
        if session.use_rich:
            filename = f"`'{filename}'`"

        self.info["last_call_header"] = _(
            "Execution stopped on line {linenumber} of file {filename}.\n"
        ).format(linenumber=linenumber, filename=filename)
        self.info["last_call_source"] = partial_source["source"]

        var_info = info_variables.get_var_info(partial_source["line"], frame)
        if var_info:
            self.info["last_call_variables"] = var_info

    def locate_parsing_error(self):
        """Sets the values of the attributes:

        * parsing_error
        * parsing_source_error
        """
        _ = current_lang.translate
        value = self.tb_data.value
        filepath = value.filename
        if filepath in ["<unknown>", "<string>"]:
            self.info["parsing_error"] = _(
                "`{filename}` is not a regular Python file whose contents\n"
                "can be analyzed.\n"
            ).format(filename=filepath)
            return

        statement = self.tb_data.statement
        statement.format_statement()
        partial_source = statement.formatted_partial_source

        if "-->" in partial_source:
            self.info["parsing_error"] = _(
                "Python could not understand the code in the file\n"
                "'{filename}'\n"
                "beyond the location indicated by ^.\n"
            ).format(filename=path_utils.shorten_path(filepath))
        elif "unexpected EOF while parsing" in repr(value):
            self.info["parsing_error"] = _(
                "Python could not understand the code the file\n"
                "'{filename}'.\n"
                "It reached the end of the file and expected more content.\n"
            ).format(filename=path_utils.shorten_path(filepath))
        elif filepath:  # could be None
            self.info["parsing_error"] = _(
                "Python could not understand the code in the file\n"
                "'{filename}'\n"
                "for an unspecified reason.\n"
            ).format(filename=path_utils.shorten_path(filepath))

        self.info["parsing_error_source"] = f"{partial_source}\n"

    def assign_tracebacks(self):
        """When required, a standard Python traceback might be required to be
        included as part of the information shown to the user.
        This function does the required formatting.

        This function defines 3 traceback:
        1. The standard Python traceback, given by Python
        2. A "simulated" Python traceback, which is essentially the same as
           the one given by Python, except that it excludes modules from this
           project.  In addition, for RecursionError, this traceback is often
           further shortened, compared with a normal Python traceback.
        3. A potentially shortened traceback, which does not include too much
           output so as not to overwhelm beginners. It also include information
           about the code on any line mentioned.

        These are given by the attributes:

        * original_python_traceback
        * simulated_python_traceback
        * shortened_traceback
        """
        _ = current_lang.translate
        if not hasattr(self, "message"):
            self.assign_message()
        suppressed = ["\n       ... " + _("More lines not shown.") + " ...\n"]

        python_tb = [line.rstrip() for line in self.tb_data.formatted_tb]

        tb = self.create_traceback()
        shortened_tb = tb[0:2] + suppressed + tb[-7:] if len(tb) > 10 else tb[:]
        pattern = re.compile(r'File "(.*)", ')
        temp = []
        for line in shortened_tb:
            match = re.search(pattern, line)
            if match:
                line = line.replace(
                    match.group(1), path_utils.shorten_path(match.group(1))
                )
            temp.append(line)
        shortened_tb = temp

        header = "Traceback (most recent call last):"  # not included in records
        if python_tb[0].startswith(header):
            tb.insert(0, header)
            shortened_tb.insert(0, header)

        if "RecursionError" in python_tb[-1]:
            tb = []
            exclude = False
            for line in python_tb:  # excluding our own code
                if exclude and line.strip() == "exec(code, self.locals)":
                    continue
                exclude = any(str(filename) in line for filename in EXCLUDED_FILE_PATH)
                if exclude:
                    continue
                tb.append(line)
            if len(tb) > 12:
                tb = tb[0:4] + suppressed + tb[-5:]

        exc = self.tb_data.value
        if exc.__cause__ or exc.__context__:
            chain_info = self.process_exception_chain(self.tb_data.exception_type, exc)
        else:
            chain_info = ""

        self.info["simulated_python_traceback"] = chain_info + "\n".join(tb) + "\n"
        self.info["shortened_traceback"] = chain_info + "\n".join(shortened_tb) + "\n"
        self.info["original_python_traceback"] = (
            chain_info + "\n".join(python_tb) + "\n"
        )
        # The following is needed for some determining the cause in at
        # least one case.
        # skipcq: PYL-W0201
        self.tb_data.simulated_python_traceback = "\n".join(tb) + "\n"

    @staticmethod
    def process_exception_chain(etype, value):
        """Adds info about exceptions raised while treating other exceptions."""
        seen = set()
        lines = []

        direct_cause = (
            "The above exception was the direct cause of the following exception:"
        )
        another_exception = (
            "During handling of the above exception, another exception occurred:"
        )

        def chain_exc(typ, exc, tb):
            seen.add(id(exc))
            context = exc.__context__
            cause = exc.__cause__
            if cause is not None and id(cause) not in seen:
                chain_exc(type(cause), cause, cause.__traceback__)
                lines.append(f"\n    {direct_cause}\n\n")
            elif (
                context is not None
                and not exc.__suppress_context__
                and id(context) not in seen
            ):
                chain_exc(type(context), context, context.__traceback__)
                lines.append(f"\n    {another_exception}\n\n")
            if tb:
                tbe = traceback.extract_tb(tb)
                lines.append("Traceback (most recent call last):\n")
                for line in traceback.format_list(tbe):
                    lines.append(line)
                for line in traceback.format_exception_only(typ, exc):
                    lines.append(line)

        chain_exc(etype, value, None)
        return "".join(lines)

    def create_traceback(self):
        """Using records that exclude code from certain files,
        creates a list from which a standard-looking traceback can
        be created.
        """
        result = []
        for record in self.tb_data.records:
            frame, filename, linenumber, _func, lines, index = record
            partial_source = get_partial_source(filename, linenumber, lines, index)
            result.append(
                '  File "{}", line {}, in {}'.format(filename, linenumber, _func)
            )
            bad_line = partial_source["line"]
            if bad_line is not None:
                result.append("    {}".format(bad_line.strip()))

        if issubclass(self.tb_data.exception_type, SyntaxError):
            value = self.tb_data.value
            offset = value.offset
            filename = value.filename
            lines = cache.get_source_lines(filename)
            result.append('  File "{}", line {}'.format(filename, value.lineno))
            _line = value.text
            if _line is None:
                try:
                    _line = lines[value.lineno - 1]
                except Exception:  # noqa
                    pass
            if _line is not None:
                if filename == "<fstring>" and lines == ["\n"]:
                    # Before Python 3.9, the traceback included a fake
                    # file for f-strings which only included parts of
                    # the f-string content.
                    cache.add(filename, _line)
                _line = _line.rstrip()
                bad_line = _line.strip()
                if bad_line:
                    offset = offset - (len(_line) - len(bad_line))  # removing indent
                    result.append("    {}".format(bad_line))
                    result.append(" " * (3 + offset) + "^")
        result.append(self.info["message"].strip())
        return result


def get_partial_source(filename, linenumber, lines, index, text_range=None):
    """Gets the part of the source where an exception occurred,
    formatted in a pre-determined way, as well as the content
    of the specific line where the exception occurred.
    """
    _ = current_lang.translate

    file_not_found = _("Problem: source of `{filename}` is not available\n").format(
        filename=filename
    )
    if filename in cache.cache:
        source, line = cache.get_formatted_partial_source(
            filename, linenumber, offset=None, text_range=text_range
        )
    elif filename and os.path.abspath(filename):
        source, line = highlight_source(
            linenumber, index, lines, offset=None, text_range=text_range
        )
        if not source:
            line = ""
            if filename == "<stdin>":
                source = "\n"  # Using a normal Python REPL - source unavailable.
                # An appropriate error message will have been given via
                # cannot_analyze_stdin
            else:
                source = file_not_found
                debug_helper.log("Problem in get_partial_source().")
                debug_helper.log(file_not_found)
    elif not filename:
        source = file_not_found
        line = ""
        debug_helper.log("Problem in get_partial_source().")
        debug_helper.log(file_not_found)
    else:
        source = line = ""
        debug_helper.log("Problem in get_partial_source().")
        debug_helper.log("Should not have reached this option")
        debug_helper.log_error()

    if not source.endswith("\n"):
        source += "\n"

    return {"source": source, "line": line}


def cannot_analyze_stdin():
    """Typical case: friendly is imported in an ordinary Python
    interpreter (REPL), and the user does not activate the friendly
    console.
    """
    _ = current_lang.translate
    return _(
        "Unfortunately, no additional information is available:\n"
        "the content of file '<stdin>' is not accessible.\n"
        "Are you using a regular Python console instead of a Friendly-console?\n"
    )
