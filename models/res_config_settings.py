from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ai_bot_groq_api_key = fields.Char(
        string='Groq API Key',
        config_parameter='ai_bot.groq_api_key',
    )
