import pathlib
from unittest import mock

import pytest

from strongarm.macho import MachoAnalyzer, MachoParser, ObjcClass, ObjcSelector, ObjcSelref, VirtualMemoryPointer
from strongarm.objc import (
    ObjcFunctionAnalyzer,
    ObjcInstruction,
    ObjcMethodInfo,
    ObjcUnconditionalBranchInstruction,
    RegisterContentsType,
)
from strongarm.objc.objc_analyzer import _demangle_cpp_symbol, _is_mangled_cpp_symbol


class TestFunctionAnalyzer:
    FAT_PATH = pathlib.Path(__file__).parent / "bin" / "StrongarmTarget"
    TEST_BINARY_PATH = pathlib.Path(__file__).parent / "bin" / "TestBinary1"

    OBJC_RETAIN_STUB_ADDR = 0x1000067CC
    SEC_TRUST_EVALUATE_STUB_ADDR = 0x100006760

    URL_SESSION_DELEGATE_IMP_ADDR = 0x100006420

    def setup_method(self) -> None:
        parser = MachoParser(TestFunctionAnalyzer.FAT_PATH)
        self.binary = parser.slices[0]
        self.analyzer = MachoAnalyzer.get_analyzer(self.binary)

        self.implementations = self.analyzer.get_imps_for_sel("URLSession:didReceiveChallenge:completionHandler:")
        self.instructions = self.implementations[0].instructions

        self.imp_addr = self.instructions[0].address
        assert self.imp_addr == TestFunctionAnalyzer.URL_SESSION_DELEGATE_IMP_ADDR

        self.function_analyzer = ObjcFunctionAnalyzer(self.binary, self.instructions)

    def test_call_targets(self) -> None:
        for i in self.function_analyzer.call_targets:
            # if no destination address, it can only be an external objc_msgSend call
            if not i.destination_address:
                assert i.is_msgSend_call
                assert i.is_external_objc_call
                assert i.selref is not None
                assert i.symbol is not None

        external_targets = {
            0x1000067CC: "_objc_retain",
            0x1000067A8: "_objc_msgSend",
            0x1000067C0: "_objc_release",
            0x1000067D8: "_objc_retainAutoreleasedReturnValue",
            TestFunctionAnalyzer.SEC_TRUST_EVALUATE_STUB_ADDR: "_SecTrustEvaluate",
        }
        local_targets = [0x100006504, 0x100006518]  # loc_100006504  # loc_100006518

        for target in self.function_analyzer.call_targets:
            if not target.destination_address:
                assert target.is_external_objc_call
            else:
                assert target.destination_address in list(external_targets.keys()) + local_targets
                if target.is_external_c_call:
                    correct_sym_name = external_targets[target.destination_address]
                    assert target.symbol == correct_sym_name

    def test_get_register_contents_at_instruction(self) -> None:
        from strongarm.objc import RegisterContentsType

        first_instr = self.function_analyzer.get_instruction_at_index(0)
        contents = self.function_analyzer.get_register_contents_at_instruction("x4", ObjcInstruction(first_instr))
        assert contents.type == RegisterContentsType.UNKNOWN

        another_instr = self.function_analyzer.get_instruction_at_index(16)
        contents = self.function_analyzer.get_register_contents_at_instruction("x1", ObjcInstruction(another_instr))
        assert contents.type == RegisterContentsType.IMMEDIATE
        assert contents.value == 0x1000090C0

    def test_get_register_contents_at_instruction_same_reg(self) -> None:
        """Test cases for dataflow where a single register has an immediate, then has a 'data link' from the same reg.
        Related ticket: SCAN-577-dataflow-fix
        """
        # Given I provide assembly where an address is loaded via a page load + page offset, using the same register
        # 0x000000010000428c    adrp       x1, #0x10011a000
        # 0x0000000100004290    add        x1, x1, #0x9c8
        binary = MachoParser(TestFunctionAnalyzer.TEST_BINARY_PATH).get_arm64_slice()
        assert binary

        function_analyzer = ObjcFunctionAnalyzer.get_function_analyzer_for_signature(
            binary, "AppDelegate", "application:didFinishLaunchingWithOptions:"
        )
        instruction = ObjcInstruction.parse_instruction(
            function_analyzer, function_analyzer.get_instruction_at_address(VirtualMemoryPointer(0x100004290))
        )
        # If I ask for the contents of the register
        contents = function_analyzer.get_register_contents_at_instruction("x1", instruction)
        # Then I get the correct value
        assert contents.type == RegisterContentsType.IMMEDIATE
        assert contents.value == 0x10011A9C8

        # Another test case with the same assumptions
        # Given I provide assembly where an address is loaded via a page load + page offset, using the same register
        # 0x0000000100004744    adrp       x8, #0x100115000
        # 0x0000000100004748    ldr        x8, [x8, #0x60]
        instruction = ObjcInstruction.parse_instruction(
            function_analyzer, function_analyzer.get_instruction_at_address(VirtualMemoryPointer(0x100004748))
        )
        # If I ask for the contents of the register
        contents = function_analyzer.get_register_contents_at_instruction("x8", instruction)
        # Then I get the correct value
        assert contents.type == RegisterContentsType.IMMEDIATE
        assert contents.value == 0x100115060

    def test_get_selref(self) -> None:
        objc_msgSendInstr = ObjcInstruction.parse_instruction(self.function_analyzer, self.instructions[16])
        assert isinstance(objc_msgSendInstr, ObjcUnconditionalBranchInstruction)
        selref = self.function_analyzer.get_objc_selref(objc_msgSendInstr)
        assert selref == 0x1000090C0

        non_branch_instruction = ObjcInstruction.parse_instruction(self.function_analyzer, self.instructions[15])
        with pytest.raises(ValueError):
            self.function_analyzer.get_objc_selref(non_branch_instruction)  # type: ignore

    def test_three_op_add(self) -> None:
        # 0x000000010000665c         adrp       x0, #0x102a41000
        # 0x0000000100006660         add        x0, x0, #0x458
        # 0x0000000100006664         bl         0x101f8600c
        three_op_binary = pathlib.Path(__file__).parent / "bin" / "ThreeOpAddInstruction"
        binary = MachoParser(three_op_binary).get_arm64_slice()
        assert binary
        analyzer = MachoAnalyzer.get_analyzer(binary)
        function_analyzer = ObjcFunctionAnalyzer(
            binary, analyzer.get_function_instructions(VirtualMemoryPointer(0x10000665C))
        )
        target_instr = function_analyzer.get_instruction_at_address(VirtualMemoryPointer(0x100006664))
        wrapped_instr = ObjcInstruction.parse_instruction(function_analyzer, target_instr)
        contents = function_analyzer.get_register_contents_at_instruction("x0", wrapped_instr)
        assert contents.type == RegisterContentsType.IMMEDIATE
        assert contents.value == 0x102A41458

    def test_get_functions(self) -> None:
        # Given the list of functions in an analyzed binary
        found_functions = self.analyzer.get_functions()
        # The list contains all of the expected addresses
        expected_addresses = [
            "0x100006228",
            "0x100006284",
            "0x100006308",
            "0x1000063b0",
            "0x1000063e8",
            "0x100006420",
            "0x100006534",
            "0x100006590",
            "0x1000065ec",
            "0x10000665c",
            "0x1000066dc",
            "0x1000066e4",
            "0x1000066e8",
            "0x1000066ec",
            "0x1000066f0",
            "0x1000066f4",
            "0x1000066f8",
            "0x100006708",
            "0x10000671c",
            "0x10000671c",
            "0x10000671c",
            "0x10000671c",
            "0x10000671c",
            "0x10000671c",
            "0x10000671c",
            "0x10000671c",
        ]
        assert set([hex(f) for f in found_functions]) == set(expected_addresses)

    def test_get_objc_methods(self) -> None:
        # Given the list of objective-c methods in an analyzed binary
        found_methods = self.analyzer.get_objc_methods()
        # The list contains all of the expected addresses
        expected_addresses = [
            "0x100006228",
            "0x100006284",
            "0x100006308",
            "0x1000063b0",
            "0x1000063e8",
            "0x100006420",
            "0x100006534",
            "0x100006590",
            "0x1000065ec",
            "0x1000066dc",
            "0x1000066e4",
            "0x1000066e8",
            "0x1000066ec",
            "0x1000066f0",
            "0x1000066f4",
            "0x10000671c",
            "0x1000066f8",
            "0x100006708",
        ]
        assert set([hex(f.imp_addr) for f in found_methods if f.imp_addr]) == set(expected_addresses)

    def test_get_symbol_name_objc(self) -> None:
        sel = ObjcSelector(
            "testMethod:",
            ObjcSelref(VirtualMemoryPointer(0), VirtualMemoryPointer(0), "testMethod:"),
            VirtualMemoryPointer(0),
        )
        method_info = ObjcMethodInfo(ObjcClass({}, "TestClass", [sel]), sel, VirtualMemoryPointer(0))  # type: ignore
        analyzer = ObjcFunctionAnalyzer(self.binary, self.instructions, method_info)

        symbol_name = analyzer.get_symbol_name()
        assert symbol_name == "-[TestClass testMethod:]"

    def test_get_symbol_name_exported_c_function(self) -> None:
        # Given a function analyzer which is associated with an exported symbol name
        with mock.patch("strongarm.macho.MachoStringTableHelper.get_symbol_name_for_address", return_value="_strlen"):
            # Then I read the correct C symbol name
            assert self.function_analyzer.get_symbol_name() == "_strlen"

    def test_get_symbol_name_anonymous_c_function(self) -> None:
        # Given a function analyzer which does not have an associated symbol name
        with mock.patch("strongarm.macho.MachoStringTableHelper.get_symbol_name_for_address", return_value=None):
            # Then the code location is reported as "_unsymbolicated_function"
            assert self.function_analyzer.get_symbol_name() == "_unsymbolicated_function"

    def test_get_symbol_name_cpp_function(self) -> None:
        # Given a function analyzer which is given a mangled C++ symbol name
        with mock.patch(
            "strongarm.macho.MachoStringTableHelper.get_symbol_name_for_address",
            return_value="__ZNK3MapI10StringName3RefI8GDScriptE10ComparatorIS0_" "E16DefaultAllocatorE3hasERKS0_",
        ):
            # Then the code location is reported as the demangled symbol name
            assert (
                self.function_analyzer.get_symbol_name() == "Map<StringName, Ref<GDScript>, Comparator<StringName>, "
                "DefaultAllocator>::has(StringName const&) const"
            )

    def test_identify_mangled_cpp_symbol(self) -> None:
        # Check identification of C++ mangled symbols
        assert _is_mangled_cpp_symbol(
            "__ZNK3MapI10StringName3RefI8GDScriptE10ComparatorIS0_" "E16DefaultAllocatorE3hasERKS0_"
        )
        assert _is_mangled_cpp_symbol("___Z5test1v_block_invoke")
        assert not _is_mangled_cpp_symbol("_strlen")

    def test_demangle_cpp_symbol(self) -> None:
        # Check expected demangling of mangled C++ symbols
        assert (
            _demangle_cpp_symbol(
                "__ZNK3MapI10StringName3RefI8GDScriptE10ComparatorIS0_" "E16DefaultAllocatorE3hasERKS0_"
            )
            == "Map<StringName, Ref<GDScript>, Comparator<StringName>, "
            "DefaultAllocator>::has(StringName const&) const"
        )

    def test_demangle_cpp_block(self) -> None:
        # Given a function analyzer which represents an Objective-C block within a C++ source function
        # This symbol also has 3 leading underscores
        with mock.patch(
            "strongarm.macho.MachoStringTableHelper.get_symbol_name_for_address",
            return_value="___Z5test1v_block_invoke",
        ):
            # Then the code location returns the properly formatted symbol name
            assert self.function_analyzer.get_symbol_name() == "block in test1()"

    def test_demangle_numbered_cpp_block(self) -> None:
        # Given a function analyzer which represents a numbered Objective-C block within a C++ source function
        with mock.patch(
            "strongarm.macho.MachoStringTableHelper.get_symbol_name_for_address",
            return_value="___Z5test1v_block_invoke2",
        ):
            # Then the code location returns the properly formatted symbol name
            assert self.function_analyzer.get_symbol_name() == "block 2 in test1()"

    def test_demangle_misleading_symbol(self) -> None:
        # Given a function analyzer which represents a symbol which looks like a mangled C++ symbol, but isn't one
        with mock.patch(
            "strongarm.macho.MachoStringTableHelper.get_symbol_name_for_address", return_value="__ZappBrannigan"
        ):
            # Then the code location is reported as the original symbol name
            assert self.function_analyzer.get_symbol_name() == "__ZappBrannigan"
