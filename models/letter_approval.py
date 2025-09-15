from odoo import api, fields, models
from odoo.exceptions import UserError


# Approval Category Class (To add "Letter Approval" as an approval category and "Addressed To" radio button)
class ApprovalCategory(models.Model):
    _inherit = 'approval.category'

    CATEGORY_SELECTION = [
        ('required', 'Required'),
        ('optional', 'Optional'),
        ('no', 'None')]

    is_addressed_to = fields.Selection(
        CATEGORY_SELECTION, string="Addressed To", default="required", required=True,
        help="Personnel/Organization this letter is addressed to (Only in a Letter Approval Request)")


# Approval Request Class (To add Addressed To & Special Content text fields)
class ApprovalRequest(models.Model):
    _inherit = 'approval.request'

    is_addressed_to = fields.Selection(related="category_id.is_addressed_to")
    addressed_to = fields.Text(string="Addressed To")
    special_content = fields.Text(string="Special Content")

    letter_ids = fields.One2many('letter.letter','approval_request_id')



# LETTER LETTER OVERRIDE (Modular)
class LetterLetter(models.Model):
    _inherit = 'letter.letter'

    # status = fields.Selection([('draft', 'Draft'), ('issued', 'Issued'), ('downloaded', 'Downloaded')], readonly=True,
    #                           string="Status", default='draft')
    status = fields.Selection([
        ('draft', 'Draft'),
        ('issued', 'Issued'),
        ('downloaded', 'Downloaded'),
        ('rejected','Rejected')],
        string="Status",
        default='draft'
    )

    # approver_ids = fields.One2many('approval.approver', 'request_id', string="Approvers", check_company=True,
    #                                compute='_compute_approver_ids', store=True, readonly=False)

    approval_request_id = fields.Many2one('approval.request',string="Approval Request", copy=False)
    request_owner_name= fields.Char(related='approval_request_id.request_owner_id.name')
    can_submit = fields.Boolean(compute='_compute_can_submit')

    _sql_constraints = [
        ('unique_approval_request_letter',
         'UNIQUE(approval_request_id)',
         'Each approval request can only be linked to one letter.'),
    ]

    def reject_action(self):
        for record in self:
            if not record.approval_request_id:
                raise UserError("Before you reject a letter, you have to select an approval request")
            # If user is one of the approvers
            if record.approval_request_id.request_status == 'pending' and record.env.user in record.approval_request_id.mapped('approver_ids.user_id'):
                user_approver = record.approval_request_id.approver_ids.filtered(lambda a: a.user_id == record.env.user)
                current_approver = record.approval_request_id.approver_ids.filtered(lambda a: a.status == 'pending')
                if user_approver.status == 'pending':
                    record.approval_request_id.action_refuse()
                elif user_approver.status == 'waiting':
                    raise UserError(f'You cannot reject before the previous approver. Current approver: {current_approver.user_id.name}')
            if record.approval_request_id.request_status == 'approved':
                record.status = 'issued'
            if record.approval_request_id.request_status == 'refused':
                record.status = 'rejected'
            return {
                'type': 'ir.actions.client',
                'tag': 'reload',
            }

    def submit_action(self):
        self.ensure_one()
        if not self.approval_request_id:
            raise UserError("Before you issue a letter, you have to select an approval request")
        user_approver = self.approval_request_id.approver_ids.filtered(lambda a: a.user_id == self.env.user)
        current_approver = self.approval_request_id.approver_ids.filtered(lambda a: a.status == 'pending')
        if user_approver.status == 'pending':
            user_approver.action_approve()
        elif user_approver.status == 'waiting':
            raise UserError(f'You cannot approve before the previous approver. Current approver: {current_approver.user_id.name}')
        if self.approval_request_id.request_status == 'approved':
            self.status = 'issued'
        if self.approval_request_id.request_status == 'refused':
            self.status = 'rejected'
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    @api.depends('approval_request_id.approver_ids.status')
    def _compute_can_submit(self):
        for record in self:
            user = record.env.user
            approvers = record.approval_request_id.approver_ids
            # filter approvers assigned to this user
            user_approvers = approvers.filtered(lambda a: a.user_id == user)
            # If user is an approver AND hasn't approved yet → can submit
            if user_approvers and any(a.status != 'approved' for a in user_approvers):
                record.can_submit = True
            else:
                record.can_submit = False

    def reset_to_draft(self):
        self.ensure_one()
        if self.status != 'draft':
            return {
                'type': 'ir.actions.act_window',
                'name': 'Confirm Reset',
                'res_model': 'letter.reset.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {'default_letter_id': self.id},
            }

    def print_letter(self):
        self.ensure_one()
        for record in self:
            # TO BE ADDED ON LATER (You need to affiliate a letter with the request)
            if record.status == 'issued' and record.env.user == record.approval_request_id.request_owner_id:
                # Bypass access rights ONLY for this update, because the security rule prevents this user from writing in the record.
                record.sudo().write({'status': 'downloaded'})
            elif record.status == 'draft':
                raise UserError('You cannot download this letter until you issue it first. Click "Submit".')
            else:
                pass
            if record.status == 'issued' or record.status == 'downloaded':
                return super().print_letter()

    def save_letter_docx(self):
        for record in self:
            if record.status == 'issued' and record.env.user == record.approval_request_id.request_owner_id:
                # Bypass access rights ONLY for this update, because the security rule prevents this user from writing in the record.
                record.sudo().write({'status': 'downloaded'})
            elif record.status == 'draft':
                raise UserError('You cannot download this letter until you issue it first. Click "Submit".')
            else:
                pass
        return super().save_letter_docx()


class LetterResetWizard(models.TransientModel):
    _name = 'letter.reset.wizard'
    _description = 'Reset Letter to Draft Confirmation'

    letter_id = fields.Many2one('letter.letter', required=True, readonly=True)

    def action_confirm_reset(self):
        """User clicked Yes"""
        self.ensure_one()
        self.letter_id.approval_request_id.action_draft()
        self.letter_id.status = 'draft'
        self.letter_id.approval_request_id = None
        return {'type': 'ir.actions.act_window_close'}

    def action_cancel(self):
        """User clicked No"""
        return {'type': 'ir.actions.act_window_close'}