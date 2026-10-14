from typing import List, Tuple, Type, Dict
import re
import langid
from transformers import MarianMTModel, MarianTokenizer

from sources.utility import pretty_print, animate_thinking
from sources.logger import Logger

class LanguageUtility:
    """LanguageUtility for language, or emotion identification"""
    def __init__(self, supported_language: List[str] = ["en", "fr", "zh"]):
        """
        Initialize the LanguageUtility class
        args:
            supported_language: list of languages for translation, determine which Helsinki-NLP model to load
        """
        self.translators_tokenizer = None 
        self.translators_model = None
        self.logger = Logger("language.log")
        self.supported_language = supported_language
        self.load_model()
    
    def load_model(self) -> None:
        animate_thinking("Loading language utility...", color="status")
        self.translators_tokenizer = {lang: MarianTokenizer.from_pretrained(f"Helsinki-NLP/opus-mt-{lang}-en") for lang in self.supported_language if lang != "en"}
        self.translators_model = {lang: MarianMTModel.from_pretrained(f"Helsinki-NLP/opus-mt-{lang}-en") for lang in self.supported_language if lang != "en"}
    
    def detect_language(self, text: str) -> str:
        """
        Detect the language of the given text using langdetect
        Limited to the supported languages list because of the model tendency to mistake similar languages
        Args:
            text: string to analyze
        Returns: ISO639-1 language code
        """
        langid.set_languages(self.supported_language)
        lang, score = langid.classify(text)
        self.logger.info(f"Identified: {text} as {lang} with conf {score}")
        return lang

    def translate(self, text: str, origin_lang: str) -> str:
        """
        Translate the given text to English
        Args:
            text: string to translate
            origin_lang: ISO language code
        Returns: translated str
        """
        if origin_lang == "en":
            return text
        if origin_lang not in self.translators_tokenizer:
            pretty_print(f"Language {origin_lang} not supported for translation", color="error")
            return text
        tokenizer = self.translators_tokenizer[origin_lang]
        inputs = tokenizer(text, return_tensors="pt", padding=True)
        model = self.translators_model[origin_lang]
        translation = model.generate(**inputs)
        return tokenizer.decode(translation[0], skip_special_tokens=True)

    def analyze(self, text):
        """
        Combined analysis of language and emotion
        Args:
            text: string to analyze
        Returns: dictionary with language related information
        """
        try:
            language = self.detect_language(text)
            return {
                "language": language
            }
        except Exception as e:
            raise e

if __name__ == "__main__":
    detector = LanguageUtility()
    
    test_texts = [
        "I am so happy today!",
        "我不要去巴黎",
        "La vie c'est cool"
    ]
    for text in test_texts:
        pretty_print("Analyzing...", color="status")
        pretty_print(f"Language: {detector.detect_language(text)}", color="status")
        result = detector.analyze(text)
        trans = detector.translate(text, result['language'])
        pretty_print(f"Translation: {trans} - from: {result['language']}")