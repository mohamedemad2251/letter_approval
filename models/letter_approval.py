from odoo import api, fields, models

class ApprovalCategory(models.Model):
    _inherit = 'approval.category'

    CATEGORY_SELECTION = [
        ('required', 'Required'),
        ('optional', 'Optional'),
        ('no', 'None')]

    addressed_to = fields.Selection(
        CATEGORY_SELECTION, string="Addressed To", default="required", required=True,
        help="Personnel/Organization this letter is addressed to (Only in a Letter Approval Request)")