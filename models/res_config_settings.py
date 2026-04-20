from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ai_bot_gemini_api_key = fields.Char(
        string='Gemini API Key',
        config_parameter='ai_bot.gemini_api_key',
        
    )
