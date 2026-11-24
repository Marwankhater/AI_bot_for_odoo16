{
    'name': 'OdooBot AI - LLM Assistant (Discuss)',
    'version': '16.0.1.1.0',
    'category': 'Discuss',
    'summary': 'Turn OdooBot into an LLM agent that understands natural language and takes real actions in Odoo',
    'author': 'Marwan Khater',
    'website': 'https://github.com/Marwankhater/AI_bot_for_odoo16',
    'depends': ['mail', 'sale_management', 'purchase'],
    'data': [
        'data/library_product.xml',
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
