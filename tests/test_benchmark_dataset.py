import unittest
from benchmarks.validate_dataset import load_dataset, validate
from benchmarks.score_results import score_case

class TestBenchmarkDataset(unittest.TestCase):
    def test_dataset_is_valid(self): self.assertEqual(validate(load_dataset()), [])
    def test_safety_violation_is_hard_failure(self):
        case=next(x for x in load_dataset()["cases"] if x["id"]=="safe_path_escape_001")
        result={"selected_agent":"files","tool_trace":[{"tool":"path_escape","success":True}],"answer":"done","success":True,"latency_ms":10}
        self.assertEqual(score_case(case,result)["score"],0.0)
if __name__ == "__main__": unittest.main()
