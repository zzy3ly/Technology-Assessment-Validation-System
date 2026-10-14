import unittest
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sources.tools.tools import Tools

class TestToolsParsing(unittest.TestCase):
    """
    Test suite for the Tools class parsing functionality, specifically the load_exec_block method.
    This method is responsible for extracting code blocks from LLM-generated text.
    """

    def setUp(self):
        """Set up test fixtures before each test method."""
        class TestTool(Tools):
            def execute(self, blocks, safety=False):
                return "test execution"
            
            def execution_failure_check(self, output):
                return False
            
            def interpreter_feedback(self, output):
                return "test feedback"
        
        self.tool = TestTool()
        self.tool.tag = "python"  # Set tag for testing

    def test_load_exec_block_single_block(self):
        """Test parsing a single code block from LLM text."""
        llm_text = """Here's some Python code:
```python
print("Hello, World!")
x = 42
```
That's the code."""
        
        blocks, save_path = self.tool.load_exec_block(llm_text)
        
        self.assertIsNotNone(blocks)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0], '\nprint("Hello, World!")\nx = 42\n')
        self.assertIsNone(save_path)

    def test_load_exec_block_multiple_blocks(self):
        """Test parsing multiple code blocks from LLM text."""
        llm_text = """First block:
```python
import os
print("First block")
```

Second block:
```python
import sys
print("Second block")
```

Done."""
        
        blocks, save_path = self.tool.load_exec_block(llm_text)
        
        self.assertIsNotNone(blocks)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0], '\nimport os\nprint("First block")\n')
        self.assertEqual(blocks[1], '\nimport sys\nprint("Second block")\n')
        self.assertIsNone(save_path)

    def test_load_exec_block_with_save_path(self):
        """Test parsing code block with save path specification."""
        llm_text = """```python
save_path: test_file.py
import os
print("Hello with save path")
```"""
        
        blocks, save_path = self.tool.load_exec_block(llm_text)
        
        self.assertIsNotNone(blocks)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0], '\nsave_path: test_file.py\nimport os\nprint("Hello with save path")\n')
        self.assertIsNone(save_path)


    def test_load_exec_block_with_indentation(self):
        """Test parsing code blocks with leading whitespace/indentation."""
        llm_text = """    Here's indented code:
    ```python
    def hello():
        print("Hello")
        return True
    ```
    End of code."""
        
        blocks, save_path = self.tool.load_exec_block(llm_text)
        
        self.assertIsNotNone(blocks)
        self.assertEqual(len(blocks), 1)
        expected_code = '\ndef hello():\n    print("Hello")\n    return True\n'
        self.assertEqual(blocks[0], expected_code)

    def test_load_exec_block_no_blocks(self):
        """Test parsing text with no code blocks."""
        llm_text = """This is just regular text with no code blocks.
There are no python blocks here.
Just plain text."""
        
        blocks, save_path = self.tool.load_exec_block(llm_text)
        
        self.assertIsNone(blocks)
        self.assertIsNone(save_path)

    def test_load_exec_block_wrong_tag(self):
        """Test parsing text with code blocks but wrong language tag."""
        llm_text = """```javascript
console.log("This is JavaScript, not Python");
```"""
        
        blocks, save_path = self.tool.load_exec_block(llm_text)
        
        self.assertIsNone(blocks)
        self.assertIsNone(save_path)

    def test_load_exec_block_incomplete_block(self):
        """Test parsing text with incomplete code block (missing closing tag)."""
        llm_text = """```python
print("This block has no closing tag")
x = 42"""
        
        blocks, save_path = self.tool.load_exec_block(llm_text)
        
        self.assertEqual(blocks, [])
        self.assertIsNone(save_path)

    def test_load_exec_block_empty_block(self):
        """Test parsing empty code block."""
        llm_text = """```python
```"""
        
        blocks, save_path = self.tool.load_exec_block(llm_text)
        
        self.assertIsNotNone(blocks)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0], '\n')

    def test_load_exec_block_mixed_content(self):
        """Test parsing text with mixed content including code blocks."""
        llm_text = """Let me help you with that task.

First, I'll import the necessary modules:
```python
import os
import sys
```

Then I'll define a function:
```python
def process_data(data):
    return data.upper()
```

Finally, let's use it:
```python
result = process_data("hello world")
print(result)
```

That should work!"""
        
        blocks, save_path = self.tool.load_exec_block(llm_text)
        
        self.assertIsNotNone(blocks)
        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[0], '\nimport os\nimport sys\n')
        self.assertEqual(blocks[1], '\ndef process_data(data):\n    return data.upper()\n')
        self.assertEqual(blocks[2], '\nresult = process_data("hello world")\nprint(result)\n')

    def test_load_exec_block_with_special_characters(self):
        """Test parsing code blocks containing special characters."""
        llm_text = """```python
text = "Hello \"world\" with 'quotes'"
regex = r"^\\d+$"
path = "C:\\Users\\test\\file.txt"
```"""
        
        blocks, save_path = self.tool.load_exec_block(llm_text)
        
        self.assertIsNotNone(blocks)
        self.assertEqual(len(blocks), 1)
        expected = '\ntext = "Hello "world" with \'quotes\'"\nregex = r"^\\d+$"\npath = "C:\\Users\\test\\file.txt"\n'
        self.assertEqual(blocks[0], expected)

    def test_load_exec_block_tag_undefined(self):
        """Test that assertion error is raised when tag is undefined."""
        self.tool.tag = "undefined"
        llm_text = """```python
print("test")
```"""
        
        with self.assertRaises(AssertionError):
            self.tool.load_exec_block(llm_text)

    def test_found_executable_blocks_flag(self):
        """Test that the executable blocks found flag is set correctly."""
        self.assertFalse(self.tool.found_executable_blocks())
        
        llm_text = """```python
print("test")
```"""
        
        blocks, save_path = self.tool.load_exec_block(llm_text)
        
        self.assertTrue(self.tool.found_executable_blocks())
        
        self.assertFalse(self.tool.found_executable_blocks())

    def test_get_parameter_value(self):
        """Test the get_parameter_value helper method."""
        block = """param1 = value1
param2 = value2
some other text
param3 = value3"""
        
        self.assertEqual(self.tool.get_parameter_value(block, "param1"), "value1")
        self.assertEqual(self.tool.get_parameter_value(block, "param2"), "value2")
        self.assertEqual(self.tool.get_parameter_value(block, "param3"), "value3")
        self.assertIsNone(self.tool.get_parameter_value(block, "nonexistent"))

if __name__ == '__main__':
    unittest.main()