"""Integration tests inside Odoo. The model is replaced by a stub, so no key or network is needed.

    odoo-bin -d test_db -i ai_bot_for_odoo16 --test-tags /ai_bot_for_odoo16 --stop-after-init
"""

from unittest.mock import patch

from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase

from .. import bot_logic as logic

CALL = "odoo.addons.ai_bot_for_odoo16.models.mail_bot.MailBot._ai_call_groq"


@tagged("post_install", "-at_install")
class TestMailBot(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("ai_bot.groq_api_key", "test-key")
        cls.salesman = new_test_user(cls.env, login="bot_salesman", groups="sales_team.group_sale_salesman,base.group_user")
        cls.plain_user = new_test_user(cls.env, login="bot_plain", groups="base.group_user")
        cls.contact_manager = new_test_user(
            cls.env, login="bot_contacts", groups="sales_team.group_sale_salesman,base.group_partner_manager,base.group_user"
        )
        cls.customer = cls.env["res.partner"].create({"name": "Mohamed Samir"})
        cls.product = cls.env["product.product"].create({"name": "Sales Book", "list_price": 520.0, "sale_ok": True})

    def _chat(self, user, *messages, model_replies=()):
        """Post messages to the user's OdooBot chat; the model answers with `model_replies` in order."""
        env = self.env(user=user)
        odoobot = env.ref("base.partner_root")
        channel = env["mail.channel"].channel_get([odoobot.id])
        channel = env["mail.channel"].browse(channel["id"])
        replies = iter(model_replies)
        with patch(CALL, side_effect=lambda *a, **k: next(replies)):
            for text in messages:
                channel.message_post(body=text, message_type="comment", subtype_xmlid="mail.mt_comment")
        bot_messages = channel.message_ids.filtered(lambda m: m.author_id == odoobot).sorted("id")
        return [logic.normalize_digits(m.body.replace("<br>", "\n")) for m in bot_messages]

    def test_quotation_needs_a_yes_before_it_exists(self):
        quote = {"action": "confirm_pending", "operation": "create_quotation",
                 "data": {"partner_name": "Mohamed Samir", "product_name": "Sales Book", "qty": 3}}
        before = self.env["sale.order"].search_count([])
        answers = self._chat(self.salesman, "quotation for mohamed, 3 sales books", model_replies=[quote])
        self.assertIn(logic.CONFIRM_MARKER, answers[-1])
        self.assertIn("1560.00", answers[-1])
        self.assertEqual(self.env["sale.order"].search_count([]), before, "nothing is created before the yes")

        self._chat(self.salesman, "نعم")
        order = self.env["sale.order"].search([("partner_id", "=", self.customer.id)], order="id desc", limit=1)
        self.assertEqual(order.order_line.product_uom_qty, 3)
        self.assertEqual(order.order_line.price_unit, 520.0)
        self.assertEqual(order.create_uid, self.salesman, "created as the user, not as the superuser")

    def test_model_asking_to_execute_still_gets_a_summary(self):
        execute = {"action": "execute_pending", "operation": "create_customer", "data": {"name": "Sneaky", "phone": "1"}}
        answers = self._chat(self.contact_manager, "add customer Sneaky", model_replies=[execute])
        self.assertIn(logic.CONFIRM_MARKER, answers[-1])
        self.assertFalse(self.env["res.partner"].search([("name", "=", "Sneaky")]))

    def test_no_cancels(self):
        create = {"action": "confirm_pending", "operation": "create_customer", "data": {"name": "Cancelled Co", "phone": "1"}}
        answers = self._chat(self.contact_manager, "add Cancelled Co", "لا", model_replies=[create])
        self.assertIn("لغيت", answers[-1])
        self.assertFalse(self.env["res.partner"].search([("name", "=", "Cancelled Co")]))

    def test_yes_without_a_summary_goes_to_the_model(self):
        chat = {"action": "chat", "message": "نعم إيه؟"}
        answers = self._chat(self.salesman, "نعم", model_replies=[chat])
        self.assertIn("نعم إيه", answers[-1])

    def test_names_are_saved_as_shown(self):
        create = {"action": "confirm_pending", "operation": "create_customer", "data": {"name": "سامر سمير", "phone": "055889"}}
        answers = self._chat(self.contact_manager, "ضيف عميل سامر سمير 055889", "نعم", model_replies=[create])
        self.assertTrue(self.env["res.partner"].search([("name", "=", "سامر سمير"), ("phone", "=", "055889")]), answers)

    def test_salesperson_without_contact_rights_cannot_create_contacts(self):
        # With sudo() this used to succeed for anyone who could chat with the bot.
        create = {"action": "confirm_pending", "operation": "create_customer", "data": {"name": "Not Allowed", "phone": "1"}}
        answers = self._chat(self.salesman, "add Not Allowed", "نعم", model_replies=[create])
        self.assertIn("مش مسموحلك", answers[-1])
        self.assertFalse(self.env["res.partner"].search([("name", "=", "Not Allowed")]))

    def test_user_without_sales_rights_cannot_quote_through_the_bot(self):
        quote = {"action": "confirm_pending", "operation": "create_quotation",
                 "data": {"partner_name": "Mohamed Samir", "product_name": "Sales Book", "qty": 1}}
        before = self.env["sale.order"].search_count([])
        answers = self._chat(self.plain_user, "quote", "نعم", model_replies=[quote])
        self.assertIn("مش مسموحلك", answers[-1])
        self.assertEqual(self.env["sale.order"].search_count([]), before)

    def test_missing_quantity_is_asked_for(self):
        quote = {"action": "confirm_pending", "operation": "create_quotation",
                 "data": {"partner_name": "Mohamed Samir", "product_name": "Sales Book"}}
        answers = self._chat(self.salesman, "quote for mohamed", model_replies=[quote])
        self.assertIn("الكمية", answers[-1])
        self.assertNotIn(logic.CONFIRM_MARKER, answers[-1])

    def test_search_finds_the_product(self):
        search = {"action": "search_product", "name": "Sales"}
        answers = self._chat(self.salesman, "do we have sales book?", model_replies=[search])
        self.assertIn("Sales Book", answers[-1])
