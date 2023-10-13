
from abc import abstractmethod
import copy
import math
from typing import Any, List, Set, Type

from dace.frontend.fortran import ast_internal_classes
from dace.frontend.fortran.ast_transforms import NodeVisitor, NodeTransformer, ParentScopeAssigner, ScopeVarsDeclarations, par_Decl_Range_Finder, mywalk

FASTNode = Any

class IntrinsicTransformation:

    @staticmethod
    @abstractmethod
    def replaced_name(func_name: str) -> str:
        pass

    @staticmethod
    @abstractmethod
    def replace(func_name: ast_internal_classes.Name_Node, args: ast_internal_classes.Arg_List_Node, line) -> ast_internal_classes.FNode:
        pass

    @staticmethod
    def has_transformation() -> bool:
        return False

class SelectedKind(IntrinsicTransformation):

    FUNCTIONS = {
        "SELECTED_INT_KIND": "__dace_selected_int_kind",
        "SELECTED_REAL_KIND": "__dace_selected_real_kind",
    }

    @staticmethod
    def replaced_name(func_name: str) -> str:
        return SelectedKind.FUNCTIONS[func_name]

    @staticmethod
    def replace(func_name: ast_internal_classes.Name_Node, args: ast_internal_classes.Arg_List_Node, line) -> ast_internal_classes.FNode:

        if func_name.name == "__dace_selected_int_kind":
            return ast_internal_classes.Int_Literal_Node(value=str(
                math.ceil((math.log2(math.pow(10, int(args.args[0].value))) + 1) / 8)),
                                                         line_number=line)
        # This selects the smallest kind that can hold the given number of digits (fp64,fp32 or fp16)
        elif func_name.name == "__dace_selected_real_kind":
            if int(args.args[0].value) >= 9 or int(args.args[1].value) > 126:
                return ast_internal_classes.Int_Literal_Node(value="8", line_number=line)
            elif int(args.args[0].value) >= 3 or int(args.args[1].value) > 14:
                return ast_internal_classes.Int_Literal_Node(value="4", line_number=line)
            else:
                return ast_internal_classes.Int_Literal_Node(value="2", line_number=line)

        raise NotImplemented()

class LoopBasedReplacement:

    @staticmethod
    def replaced_name(func_name: str) -> str:
        replacements = {
            "SUM": "__dace_sum",
            "ANY": "__dace_any"
        }
        return replacements[func_name]

    @staticmethod
    def replace(func_name: ast_internal_classes.Name_Node, args: ast_internal_classes.Arg_List_Node, line) -> ast_internal_classes.FNode:
        func_types = {
            "__dace_sum": "DOUBLE",
            "__dace_any": "DOUBLE"
        }
        # FIXME: Any requires sometimes returning an array of booleans
        call_type = func_types[func_name.name]
        return ast_internal_classes.Call_Expr_Node(name=func_name, type=call_type, args=args.args, line_number=line)

    @staticmethod
    def has_transformation() -> bool:
        return True

class LoopBasedReplacementVisitor(NodeVisitor):

    """
    Finds all intrinsic operations that have to be transformed to loops in the AST
    """
    def __init__(self, func_name: str):
        self._func_name = func_name
        self.nodes: List[ast_internal_classes.FNode] = []

    def visit_BinOp_Node(self, node: ast_internal_classes.BinOp_Node):

        if isinstance(node.rval, ast_internal_classes.Call_Expr_Node):
            if node.rval.name.name == self._func_name:
                self.nodes.append(node)

    def visit_Execution_Part_Node(self, node: ast_internal_classes.Execution_Part_Node):
        return

class LoopBasedReplacementTransformation(NodeTransformer):

    """
    Transforms the AST by removing intrinsic call and replacing it with loops
    """
    def __init__(self, ast):
        self.count = 0
        ParentScopeAssigner().visit(ast)
        self.scope_vars = ScopeVarsDeclarations()
        self.scope_vars.visit(ast)

        self.rvals = []


    @abstractmethod
    def func_name(self) -> str:
        pass

    def visit_Execution_Part_Node(self, node: ast_internal_classes.Execution_Part_Node):

        newbody = []
        for child in node.execution:
            lister = LoopBasedReplacementVisitor(self.func_name())
            lister.visit(child)
            res = lister.nodes

            if res is None or len(res) == 0:
                newbody.append(self.visit(child))
                continue

            self.loop_ranges = []
            # We need to reinitialize variables as the class is reused for transformation between different
            # calls to the same intrinsic.
            self._initialize()

            # Visit all intrinsic arguments and extract arrays
            for i in mywalk(child.rval):
                if isinstance(i, ast_internal_classes.Call_Expr_Node) and i.name.name == self.func_name():
                    self._parse_call_expr_node(i, newbody)

            # Verify that all of intrinsic args are correct and prepare them for loop generation
            self._summarize_args(child, newbody)

            # Initialize the result variable
            newbody.append(self._initialize_result(child))

            # Generate the intrinsic-specific logic inside loop body
            body = self._generate_loop_body(child)

            # Now generate the multi-dimensiona loop header and updates
            range_index = 0
            for i in self.loop_ranges:
                initrange = i[0]
                finalrange = i[1]
                init = ast_internal_classes.BinOp_Node(
                    lval=ast_internal_classes.Name_Node(name="tmp_parfor_" + str(self.count + range_index)),
                    op="=",
                    rval=initrange,
                    line_number=child.line_number)
                cond = ast_internal_classes.BinOp_Node(
                    lval=ast_internal_classes.Name_Node(name="tmp_parfor_" + str(self.count + range_index)),
                    op="<=",
                    rval=finalrange,
                    line_number=child.line_number)
                iter = ast_internal_classes.BinOp_Node(
                    lval=ast_internal_classes.Name_Node(name="tmp_parfor_" + str(self.count + range_index)),
                    op="=",
                    rval=ast_internal_classes.BinOp_Node(
                        lval=ast_internal_classes.Name_Node(name="tmp_parfor_" + str(self.count + range_index)),
                        op="+",
                        rval=ast_internal_classes.Int_Literal_Node(value="1")),
                    line_number=child.line_number)
                current_for = ast_internal_classes.Map_Stmt_Node(
                    init=init,
                    cond=cond,
                    iter=iter,
                    body=ast_internal_classes.Execution_Part_Node(execution=[body]),
                    line_number=child.line_number)
                body = current_for
                range_index += 1

            newbody.append(body)

            self.count = self.count + range_index
        return ast_internal_classes.Execution_Part_Node(execution=newbody)

class Sum(LoopBasedReplacement):

    """
        In this class, we implement the transformation for Fortran intrinsic SUM(:)
        We support two ways of invoking the function - by providing array name and array subscript.
        We do NOT support the *DIM* argument.

        During the loop construction, we add a single variable storing the partial result.
        Then, we generate a binary node accumulating the result.
    """

    class Transformation(LoopBasedReplacementTransformation):

        def __init__(self, ast):
            super().__init__(ast)

        def func_name(self) -> str:
            return "__dace_sum"

        def _initialize(self):
            self.rvals = []
            self.argument_variable = None

        def _parse_call_expr_node(self, node: ast_internal_classes.Call_Expr_Node, new_func_body: List[ast_internal_classes.FNode]):

            for arg in node.args:

                # supports syntax SUM(arr)
                if isinstance(arg, ast_internal_classes.Name_Node):
                    array_node = ast_internal_classes.Array_Subscript_Node(parent=arg.parent)
                    array_node.name = arg

                    # If we access SUM(arr) where arr has many dimensions,
                    # We need to create a ParDecl_Node for each dimension
                    dims = len(self.scope_vars.get_var(node.parent, arg.name).sizes)
                    array_node.indices = [ast_internal_classes.ParDecl_Node(type='ALL')] * dims

                    self.rvals.append(array_node)

                # supports syntax SUM(arr(:))
                if isinstance(arg, ast_internal_classes.Array_Subscript_Node):
                    self.rvals.append(arg)


        def _summarize_args(self, node: ast_internal_classes.FNode, new_func_body: List[ast_internal_classes.FNode]):

            if len(self.rvals) != 1:
                raise NotImplementedError("Only one array can be summed")

            self.argument_variable = self.rvals[0]

            par_Decl_Range_Finder(self.argument_variable, self.loop_ranges, [], [], self.count, new_func_body, self.scope_vars, True)

        def _initialize_result(self, node: ast_internal_classes.FNode) -> ast_internal_classes.BinOp_Node:

            return ast_internal_classes.BinOp_Node(
                lval=node.lval,
                op="=",
                rval=ast_internal_classes.Int_Literal_Node(value="0"),
                line_number=node.line_number
            )

        def _generate_loop_body(self, node: ast_internal_classes.FNode) -> ast_internal_classes.BinOp_Node:

            return ast_internal_classes.BinOp_Node(
                lval=node.lval,
                op="=",
                rval=ast_internal_classes.BinOp_Node(
                    lval=node.lval,
                    op="+",
                    rval=self.argument_variable,
                    line_number=node.line_number
                ),
                line_number=node.line_number
            )


class Any(LoopBasedReplacement):

    class Transformation(LoopBasedReplacementTransformation):

        def __init__(self, ast):
            super().__init__(ast)

        def func_name(self) -> str:
            return "__dace_any"

        def _parse_array(self, node: ast_internal_classes.Execution_Part_Node, arg: ast_internal_classes.FNode) -> ast_internal_classes.Array_Subscript_Node:

            # supports syntax ANY(arr)
            if isinstance(arg, ast_internal_classes.Name_Node):
                array_node = ast_internal_classes.Array_Subscript_Node(parent=arg.parent)
                array_node.name = arg

                # If we access SUM(arr) where arr has many dimensions,
                # We need to create a ParDecl_Node for each dimension
                dims = len(self.scope_vars.get_var(node.parent, arg.name).sizes)
                array_node.indices = [ast_internal_classes.ParDecl_Node(type='ALL')] * dims

                return array_node

            # supports syntax ANY(arr(:))
            if isinstance(arg, ast_internal_classes.Array_Subscript_Node):
                return arg

        def _initialize(self):
            self.rvals = []

        def _parse_call_expr_node(self, node: ast_internal_classes.Call_Expr_Node, new_func_body: List[ast_internal_classes.FNode]):

            if len(node.args) > 1:
                raise NotImplementedError("Fortran ANY with the DIM parameter is not supported!")
            arg = node.args[0]

            array_node = self._parse_array(node, arg)
            if array_node is not None:
                self.rvals.append(array_node)

                if len(self.rvals) != 1:
                    raise NotImplementedError("Only one array can be summed")
                val = self.rvals[0]
                rangeposrval = []

                par_Decl_Range_Finder(val, self.loop_ranges, [], [], self.count, new_func_body, self.scope_vars, True)
                self.cond = ast_internal_classes.BinOp_Node(op="==",
                                                    rval=ast_internal_classes.Int_Literal_Node(value="1"),
                                                    lval=copy.deepcopy(val),
                                                    line_number=node.line_number)
            else:

                # supports syntax ANY(logical op)
                # the logical op can be:
                #
                # (1) arr1 op arr2
                # where arr1 and arr2 are name node or array subscript node
                # there, we need to extract shape and verify they are the same
                #
                # (2) arr1 op scalar
                # there, we ignore the scalar because it's not an array
                if isinstance(arg, ast_internal_classes.BinOp_Node):

                    left_side_arr  = self._parse_array(node, arg.lval)
                    right_side_arr  = self._parse_array(node, arg.rval)
                    has_two_arrays = left_side_arr is not None and right_side_arr is not None

                    if not has_two_arrays:

                        # if one side of the operator is scalar, then parsing array
                        # will return none
                        dominant_array = left_side_arr
                        if left_side_arr is None:
                            dominant_array = right_side_arr

                        rangeposrval = []
                        rangeslen_left = []
                        rangeposrval = []
                        par_Decl_Range_Finder(dominant_array, self.loop_ranges, rangeposrval, rangeslen_left, self.count, new_func_body, self.scope_vars, True)
                        val = arg

                        self.cond = copy.deepcopy(val)
                        if left_side_arr is not None:
                            self.cond.lval = dominant_array
                        if right_side_arr is not None:
                            self.cond.rval = dominant_array

                        return


                    if len(left_side_arr.indices) != len(right_side_arr.indices):
                        raise TypeError("Can't parse Fortran ANY with different array ranks!")

                    for left_idx, right_idx in zip(left_side_arr.indices, right_side_arr.indices):
                        if left_idx.type != right_idx.type:
                            raise TypeError("Can't parse Fortran ANY with different array ranks!")

                    rangeposrval = []
                    rangeslen_left = []
                    rangeposrval = []
                    par_Decl_Range_Finder(left_side_arr, self.loop_ranges, rangeposrval, rangeslen_left, self.count, new_func_body, self.scope_vars, True)
                    val = arg

                    rangesrval_right = []
                    rangeslen_right = []
                    par_Decl_Range_Finder(right_side_arr, rangesrval_right, [], rangeslen_right, self.count, new_func_body, self.scope_vars, True)

                    for left_len, right_len in zip(rangeslen_left, rangeslen_right):
                        if left_len != right_len:
                            raise TypeError("Can't support Fortran ANY with different array ranks!")

                    # Now, the loop will be dictated by the left array
                    # If the access pattern on the right array is different, we need to shfit it - for every dimension.
                    # For example, we can have arr(1:3) == arr2(3:5)
                    # Then, loop_idx is from 1 to 3
                    # arr becomes arr[loop_idx]
                    # but arr2 must be arr2[loop_idx + 2]
                    for i in range(len(right_side_arr.indices)):

                        idx_var = right_side_arr.indices[i]
                        start_loop = self.loop_ranges[i][0]
                        end_loop = rangesrval_right[i][0]

                        difference = int(end_loop.value) - int(start_loop.value)
                        if difference != 0:
                            new_index = ast_internal_classes.BinOp_Node(
                                lval=idx_var,
                                op="+",
                                rval=ast_internal_classes.Int_Literal_Node(value=str(difference)),
                                line_number=node.line_number
                            )
                            right_side_arr.indices[i] = new_index

                    # Now, we need to convert the array to a proper subscript node
                    self.cond = copy.deepcopy(val)
                    self.cond.lval = left_side_arr
                    self.cond.rval = right_side_arr

        def _summarize_args(self, node: ast_internal_classes.FNode, new_func_body: List[ast_internal_classes.FNode]):
            pass

        def _initialize_result(self, node: ast_internal_classes.FNode) -> ast_internal_classes.BinOp_Node:

            return ast_internal_classes.BinOp_Node(
                lval=node.lval,
                op="=",
                rval=ast_internal_classes.Int_Literal_Node(value="0"),
                line_number=node.line_number
            )

        def _generate_loop_body(self, node: ast_internal_classes.FNode) -> ast_internal_classes.BinOp_Node:

            body_if = ast_internal_classes.Execution_Part_Node(execution=[
                ast_internal_classes.BinOp_Node(
                    lval=copy.deepcopy(node.lval),
                    op="=",
                    rval=ast_internal_classes.Int_Literal_Node(value="1"),
                    line_number=node.line_number
                ),
                # TODO: we should make the `break` generation conditional based on the architecture
                # For parallel maps, we should have no breaks
                # For sequential loop, we want a break to be faster
                #ast_internal_classes.Break_Node(
                #    line_number=node.line_number
                #)
            ])
            return ast_internal_classes.If_Stmt_Node(
                cond=self.cond,
                body=body_if,
                body_else=ast_internal_classes.Execution_Part_Node(execution=[]),
                line_number=node.line_number
            )

class FortranIntrinsics:

    IMPLEMENTATIONS_AST = {
        "SELECTED_INT_KIND": SelectedKind,
        "SELECTED_REAL_KIND": SelectedKind,
        "SUM": Sum,
        "ANY": Any
    }

    IMPLEMENTATIONS_DACE = {
        "__dace_selected_int_kind": SelectedKind,
        "__dace_selected_real_kind": SelectedKind,
        "__dace_sum": Sum,
        "__dace_any": Any
    }

    def __init__(self):
        self._transformations_to_run = set()

    def transformations(self) -> Set[Type[NodeTransformer]]:
        return self._transformations_to_run

    @staticmethod
    def function_names() -> List[str]:
        return list(FortranIntrinsics.IMPLEMENTATIONS_DACE.keys())

    def replace_function_name(self, node: FASTNode) -> ast_internal_classes.Name_Node:

        func_name = node.string
        replacements = {
            "INT": "__dace_int",
            "DBLE": "__dace_dble",
            "SQRT": "sqrt",
            "COSH": "cosh",
            "ABS": "abs",
            "MIN": "min",
            "MAX": "max",
            "EXP": "exp",
            "EPSILON": "__dace_epsilon",
            "TANH": "tanh",
            "SIGN": "__dace_sign",
            "EXP": "exp"
        }
        if func_name in replacements:
            return ast_internal_classes.Name_Node(name=replacements[func_name])
        else:

            if self.IMPLEMENTATIONS_AST[func_name].has_transformation():
                self._transformations_to_run.add(self.IMPLEMENTATIONS_AST[func_name].Transformation)

            return ast_internal_classes.Name_Node(name=self.IMPLEMENTATIONS_AST[func_name].replaced_name(func_name))

    def replace_function_reference(self, name: ast_internal_classes.Name_Node, args: ast_internal_classes.Arg_List_Node, line):

        func_types = {
            "__dace_int": "INT",
            "__dace_dble": "DOUBLE",
            "sqrt": "DOUBLE",
            "cosh": "DOUBLE",
            "abs": "DOUBLE",
            "min": "DOUBLE",
            "max": "DOUBLE",
            "exp": "DOUBLE",
            "__dace_epsilon": "DOUBLE",
            "tanh": "DOUBLE",
            "__dace_sign": "DOUBLE",
        }
        if name.name in func_types:
            # FIXME: this will be progressively removed
            call_type = func_types[name.name]
            return ast_internal_classes.Call_Expr_Node(name=name, type=call_type, args=args.args, line_number=line)
        else:
            return self.IMPLEMENTATIONS_DACE[name.name].replace(name, args, line)
