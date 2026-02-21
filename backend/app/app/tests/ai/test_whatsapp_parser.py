import unittest

from app.ai.whatsapp_parser import WhatsAppParser


class WhatsAppParserTypeTests(unittest.TestCase):
    def test_convert_ai_result_preserves_valid_type(self) -> None:
        parser = WhatsAppParser()
        transaction = parser.convert_ai_result_to_transaction(
            {"type": "income", "amount": 100, "id": "abc"}
        )
        self.assertEqual(transaction["type"], "income")

    def test_convert_ai_result_infers_transfer_from_accounts(self) -> None:
        parser = WhatsAppParser()
        transaction = parser.convert_ai_result_to_transaction(
            {
                "amount": 500,
                "id": "abc",
                "from_account": {"id": 1, "name": "BBVA"},
                "to_account": {"id": 2, "name": "NU"},
            },
            message="pasar 500 de bbva a nu",
        )
        self.assertEqual(transaction["type"], "transfer")

    def test_convert_ai_result_infers_income_from_message(self) -> None:
        parser = WhatsAppParser()
        transaction = parser.convert_ai_result_to_transaction(
            {"amount": 1800, "id": "abc"}, message="ingreso 1800 nomina"
        )
        self.assertEqual(transaction["type"], "income")


if __name__ == "__main__":
    unittest.main()
