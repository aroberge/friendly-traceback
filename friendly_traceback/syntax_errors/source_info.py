"""Contains a class that compiles and stores all the information that
is relevant to the analysis of SyntaxErrors.
"""

from ..source_cache import cache
from .syntax_utils import matching_brackets
from .. import debug_helper
from .. import token_utils


class Statement:
    """Instances of this class contain all relevant information required
    for the various functions that attempt to determine the cause of
    SyntaxError.

    The basic idea is to retrieve a "complete statement" where the
    exception is raised. By "complete statement", we mean the smallest
    number of consecutive lines of code that contains the line where
    the exception was raised and includes matching pairs of brackets,
    (), [], {}. If a problem arises due to non-matching pairs of brackets,
    this information is available (variables begin_brackets or end_bracket).

    A complete statement is saved as a list of tokens (statement_tokens)
    from which it could be reconstructed using an untokenize function.

    From this list of tokens, a secondary list is obtained (tokens) by
    removing all space-like and comment tokens, which are the only
    meaningful tokens needed to do the analysis.

    To simplify the code doing the error analysis itself, we precompute various
    parameters (e.g. does the statement fits on a single line, how many
    meaningful tokens are included, what are the first and last tokens
    on that statement, etc.) which are needed for some functions.
    """

    def __init__(self, value, bad_line):
        self.filename = value.filename
        self.linenumber = value.lineno
        self.message = value.msg
        self.offset = value.offset
        self.bad_line = bad_line  # previously obtained from the traceback
        self.statement = bad_line  # temporary assignment

        self.fstring_error = self.filename == "<fstring>" or "f-string" in self.message

        self.statement_tokens = []  # include newlines, comments, etc.
        self.tokens = []  # meaningful tokens
        self.begin_brackets = []
        self.end_bracket = None
        self.bad_token = None
        self.prev_token = None  # meaningful token preceding bad token
        self.next_token = None  # meaningful token following bad token
        self.bad_token_index = 0
        self.first_token = None
        self.last_token = None
        self.single_line = True

        if self.linenumber is not None:
            source_tokens = self.get_source_tokens()
            self.obtain_statement(source_tokens)
            self.tokens = self.remove_meaningless_tokens()
            self.statement = token_utils.untokenize(self.statement_tokens)
        elif "too many statically nested blocks" not in self.message:
            debug_helper.log("linenumber is None in analyze_syntax._find_likely_cause")

        self.nb_tokens = len(self.tokens)
        self.is_complete = not (self.begin_brackets or self.end_bracket)
        if self.nb_tokens >= 1:
            self.first_token = self.tokens[0]
            self.last_token = self.tokens[-1]
            self.single_line = (
                self.first_token.start_row == self.last_token.end_row
                and self.is_complete
            )
            if self.bad_token is None:
                self.bad_token = self.tokens[-1]
                self.bad_token_index = self.nb_tokens - 1
                if self.bad_token_index == 0:
                    self.prev_token = token_utils.tokenize("")[0]  # fake
                else:
                    self.prev_token = self.tokens[self.bad_token_index - 1]

            if self.prev_token is None:
                if self.bad_token_index == 0:
                    self.prev_token = token_utils.tokenize("")[0]  # fake
                else:
                    self.prev_token = self.tokens[self.bad_token_index - 1]

        if self.last_token != self.bad_token:
            self.next_token = self.tokens[self.bad_token_index + 1]

    def get_source_tokens(self):
        """Returns a list containing all the tokens from the source."""
        source = ""
        if "f-string: invalid syntax" in self.message:
            source = self.bad_line
            try:
                exec(self.bad_line)
            except SyntaxError as e:
                self.offset = e.offset
                self.linenumber = 1
        if not source.strip():
            source_lines = cache.get_source_lines(self.filename)
            source = "".join(source_lines)
            if not source.strip():
                source = self.bad_line or "\n"
        return token_utils.tokenize(source)

    def format_statement(self):
        """Format the statement identified as causing the problem and possibly
        a couple of preceding statements, showing the line number and token identified.
        """
        # Some errors, like "Too many statistically nested blocs" prevent
        # Python from making a source available.
        if not self.statement_tokens:
            self.formatted_partial_source = ""
            return

        last = self.tokens[-1].end_row  # meaningful tokens

        for index, statement in enumerate(self.all_statements):
            last_token = statement[-1]
            if last - last_token.end_row < 5:
                break

        keep = self.all_statements[index:]  # noqa - warnings about index not defined
        start = keep[0][0].start_row

        tokens = []
        for statement in keep:
            tokens.extend(statement)

        partial_source = token_utils.untokenize(tokens)
        lines = partial_source.split("\n")
        nb_lines = len(lines)
        new_lines = []
        nb_digits = len(str(start + nb_lines))
        no_mark = "       {:%d}: " % nb_digits
        with_mark = "    -->{:%d}: " % nb_digits

        offset_mark = " " * (8 + nb_digits + self.offset) + "^"

        marked = False
        for i, line in enumerate(lines, start):
            if i == self.linenumber:
                num = with_mark.format(i)
                new_lines.append(num + line.rstrip())
                new_lines.append(offset_mark)
                marked = True
            elif marked:
                if not line.strip():  # do not add empty lines after problem line
                    break
                num = no_mark.format(i)
                new_lines.append(num + line.rstrip())
            elif i > self.linenumber + 5:
                # avoid printing to the end of the file if we have an unclosed bracket
                break
            else:
                num = no_mark.format(i)
                new_lines.append(num + line.rstrip())

        self.formatted_partial_source = "\n".join(new_lines)

    def obtain_statement(self, source_tokens):
        """This method scans the source searching for the statement that
        caused the problem. Most often, it will be a single line of code.
        However, it might occasionally be a multiline statement that
        includes code surrounded by some brackets spanning multiple lines.

        It will set the following:

        - self.statement_tokens: a list of all the tokens in the problem statement
        - self.bad_token: the token identified as causing the problem based on offset.
        - self.begin_brackets: a list of open brackets '([{' not yet closed
        - self.end_bracket: an unmatched closing bracket ')]}' signaling an error
        - self.all_statements: list of all individual identified statements up to
          and including the problem statement.
        """

        previous_row = -1
        previous_row_non_space = -1
        previous_token = None
        self.prev_token = None
        continuation_line = False
        self.all_statements = []
        self.statement_tokens = []

        for token in source_tokens:
            # Did we collect all the tokens belonging to the statement?
            if token.start_row > self.linenumber and not continuation_line:
                if not self.begin_brackets:
                    break
                elif token.is_in(
                    [
                        "class",
                        "def",
                        "if",
                        "elif",
                        "else",
                        "for",
                        "import",
                        "try",
                        "except",
                        "finally",
                        "with" "while",
                    ]
                ):
                    if token.start_row > previous_row_non_space:
                        # the keyword above likely start a new statement
                        break
            if token.string.strip():
                previous_row_non_space = token.end_row

            # is this a new statement?
            if token.start_row > previous_row:
                if previous_token is not None:
                    continuation_line = previous_token.line.endswith("\\\n")
                if token.start_row <= self.linenumber and not self.begin_brackets:
                    if self.statement_tokens:
                        self.all_statements.append(self.statement_tokens[:])
                    self.statement_tokens = []
                previous_row = token.start_row

            self.statement_tokens.append(token)
            # The offset seems to be different depending on Python versions,
            # sometimes matching the beginning of a token, sometimes the end.
            # Furthermore, the end of a token (end_col) might be equal to
            # the beginning of the next (start_col).
            if (
                token.start_row == self.linenumber
                and token.start_col <= self.offset <= token.end_col
                and self.bad_token is None
                and token.string.strip()
            ):
                self.bad_token = token
                if self.bad_token.is_comment():
                    self.bad_token = self.prev_token
            elif (
                token.string.strip()
                and not token.is_comment()
                and self.bad_token is None
            ):
                self.prev_token = token

            previous_token = token
            if token.is_not_in("()[]}{"):
                continue

            if token.is_in("([{"):
                self.begin_brackets.append(token.string)
            elif token.is_in(")]}"):  # Does it match or not
                self.end_bracket = token
                if not self.begin_brackets:
                    break
                else:
                    open_bracket = self.begin_brackets.pop()
                    if not matching_brackets(open_bracket, token.string):
                        self.begin_brackets.append(open_bracket)
                        break
                    else:
                        self.end_bracket = None
        if self.statement_tokens:  # Protecting against EOF while parsing
            last_line = token_utils.untokenize(self.statement_tokens)
            if not last_line.strip():
                if self.all_statements:
                    self.statement_tokens = self.all_statements[-1]
            else:
                self.all_statements.append(self.statement_tokens)

    def remove_meaningless_tokens(self):
        """Given a list of tokens, remove all space-like tokens and comments."""
        index = 0
        tokens = []
        for tok in self.statement_tokens:
            if not tok.string.strip() or tok.is_comment():
                continue
            tokens.append(tok)
            if tok is self.bad_token:
                self.bad_token_index = index
            index += 1
        return tokens
