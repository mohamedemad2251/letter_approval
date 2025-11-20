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

    letter_ids = fields.One2many('letter.letter', 'approval_request_id')

    # -----------------------------------------------
    # Restrict Access For Digital & Physical Letters
    # -----------------------------------------------
    # Used to get only digital letters to show for the normal users
    digital_letter_ids = fields.One2many(
        'letter.letter',
        compute='_compute_digital_letters',
        string="Digital Letters",
        store=False,
    )

    is_request_owner = fields.Boolean(compute='_compute_is_request_owner',store=False)
    is_approver_manager = fields.Boolean(compute='_compute_is_approver_manager',store=False)

    is_letter_approval = fields.Boolean(compute='_compute_is_letter_approval',store=False)

    @api.depends('category_id')
    def _compute_is_letter_approval(self):
        for record in self:
            record.is_letter_approval = record.category_id.name and record.category_id.name == "Letter Approval"

    @api.depends('letter_ids')
    def _compute_digital_letters(self):
        for record in self:
            record.digital_letter_ids = record.letter_ids.filtered(lambda l: l.delivery_method == 'digital')

    def _compute_is_request_owner(self):
        for record in self:
            if record.request_owner_id:
                record.is_request_owner = record.request_owner_id.id == record.env.user.id

    def _compute_is_approver_manager(self):
        for record in self:
            record.is_approver_manager = record.env.user.has_group('approvals.group_approval_user') or record.env.user.has_group('approvals.group_approval_manager')

    # -----------------------------------------------
    # Choose Template Type But With Restricted Access
    # -----------------------------------------------
    template_id = fields.Many2one('letter.template',string='Letter Type')

    template_select = fields.Selection(selection='_get_template_selection',
                                       string="Letter Type",
                                        # store=True,
                                        required=True,
                                       compute='_compute_template_select',
                                       inverse='_inverse_template_select',
                                       groups="base.group_user" )
    @api.depends('template_id')
    def _compute_template_select(self):
        for record in self:
            record.template_select = record.template_id.id if record.template_id else False

    def _inverse_template_select(self):
        for record in self:
            # raise UserError(record.template_select)
            record.template_id = self.env['letter.template'].browse(record.template_select)
            # record.template_id = record.template_select

    @api.model
    def _get_template_selection(self):
        try:
            templates = self.env['letter.template'].sudo().search([])
            return [(t.id, t.template_name) for t in templates] or []
        except Exception as e:
            return []

    @api.model
    def create(self, vals):
        # If multiple records are created at once, handle each one
        if isinstance(vals, list):
            for val in vals:
                if val.get('template_select') and not val.get('template_id'):
                    val['template_id'] = val['template_select']
            return super().create(vals)

        # Single record
        if vals.get('template_select') and not vals.get('template_id'):
            vals['template_id'] = vals['template_select']
        return super().create(vals)

    # -------------------------------
    # HELPER: sync request + letter state
    # -------------------------------
    def _sync_letter_status(self):
        """Keep letters in sync with request status."""
        for request in self:
            if not request.letter_ids:
                continue
            if request.request_status == 'approved':
                request.letter_ids.status = 'issued'
            elif request.request_status == 'refused':
                request.letter_ids.status = 'rejected'
            elif request.request_status == 'cancel':
                request.letter_ids.status = 'draft'
            elif request.request_status == 'new':
                request.letter_ids.status = 'draft'
            elif request.request_status == 'pending':
                # only set pending if at least one approver is still processing
                request.letter_ids.status = 'pending'

    # -------------------------------
    # ACTIONS
    # -------------------------------
    def action_approve(self, approver=None):
        self.ensure_one()

        # Only enforce the "letter must exist before final approval" rule for the Letter Approval category
        if self.category_id.name == 'Letter Approval':
            # --- Sequenced approvals (one-by-one) ---
            if self.approver_sequence:
                # If there are NO approvers in 'waiting' state then this approver is the last in sequence.
                # In that case a letter must already exist (otherwise we block).
                waiting = self.approver_ids.filtered(lambda a: a.status == 'waiting')
                if not waiting and not self.letter_ids:
                    raise UserError(
                        'You cannot approve without creating a letter (you are the last approver). '
                        'Please create a letter first.'
                    )

            # --- Parallel approvals (not sequenced) ---
            else:
                pending_approvers = self.approver_ids.filtered(lambda a: a.status == 'pending')
                if pending_approvers and len(pending_approvers) <= 1 and not self.letter_ids:
                    raise UserError(
                        'You cannot approve as you are the last/only approver left. '
                        'Please attach/create the letter first.'
                    )

        # perform the actual approval (call the parent implementation)
        res = super().action_approve(approver=approver)

        # keep linked letters in sync with request state
        if self.category_id.name == 'Letter Approval':
            self._sync_letter_status()

        return res

    def action_refuse(self, approver=None):
        self.ensure_one()
        super().action_refuse()
        self._sync_letter_status()

    def action_withdraw(self, approver=None):
        self.ensure_one()
        super().action_withdraw()
        # When withdrawn, safest is to send back to draft so user can re-issue
        if self.letter_ids:
            approvers = self.approver_ids.filtered(lambda a: a.status == 'pending')
            if len(approvers) == self.approval_minimum:
                self.letter_ids.status = 'draft'
            else:
                self.letter_ids.status = 'pending'
            self._sync_letter_status()

    def action_create_letter(self):
        self.ensure_one()

        # Create the letter with default values
        letter = self.env['letter.letter'].create({
            'letter_name': self.name or "Approval Letter",
            'approval_request_id': self.id,
            'request_owner_name': self.request_owner_id.name if self.request_owner_id else '',
            'template_id': self.template_id.id if self.template_id else None,
            'employee_id': self.request_owner_id.employee_id.id if self.template_id and self.request_owner_id.employee_id else None,
            'delivery_method' : 'digital',      #Default
        })

        # # Immediately approve using parent logic
        # super().action_approve()

        # # Sync letter after approval
        # self._sync_letter_status()

        # Open the letter in form view
        return {
            'name': 'Letter',
            'type': 'ir.actions.act_window',
            'res_model': 'letter.letter',
            'view_mode': 'form',
            'res_id': letter.id,
            'target': 'current',
        }


# LETTER LETTER OVERRIDE (Modular)
class LetterLetter(models.Model):
    _inherit = 'letter.letter'

    # status = fields.Selection([('draft', 'Draft'), ('issued', 'Issued'), ('downloaded', 'Downloaded')], readonly=True,
    #                           string="Status", default='draft')
    status = fields.Selection([
        ('draft', 'Draft'),
        ('pending','Pending'),
        ('issued', 'Issued'),
        ('downloaded', 'Downloaded'),
        ('rejected','Rejected')],
        string="Status",
        default='draft'
    )

    # approver_ids = fields.One2many('approval.approver', 'request_id', string="Approvers", check_company=True,
    #                                compute='_compute_approver_ids', store=True, readonly=False)

    approval_request_id = fields.Many2one('approval.request',string="Request Reason", copy=False)
    request_owner_name= fields.Char(related='approval_request_id.request_owner_id.name')
    can_submit = fields.Boolean(compute='_compute_can_submit')
    can_download = fields.Boolean(compute='_compute_can_download',store=False)

    # REMOVE THIS LATER:
    template_id = fields.Many2one('letter.template', string="Template", required=False)

    addressed_to = fields.Text(related='approval_request_id.addressed_to')

    _unique_approval_request_letter = models.Constraint(
         "UNIQUE(approval_request_id)",
         "Each approval request can only be linked to one letter.")


    @api.onchange('approval_request_id')
    def _compute_employee(self):
        self.ensure_one()
        if self.approval_request_id:
            self.template_id = self.approval_request_id.template_id if self.approval_request_id.template_id else None
            if self.template_id and self.template_id.template_module == 'hr':
                self.employee_id = self.approval_request_id.request_owner_id.employee_id.id if self.approval_request_id.request_owner_id.employee_id else None
            else:
                self.employee_id = None

    @api.depends('addressed_to')
    def _compute_replaced_content(self):
        self.ensure_one()
        # Call parent implementation (letter_hr or letter.base depending on MRO) - no sudo() on super() call
        super()._compute_replaced_content()
        # Use sudo() only when accessing protected template_id field
        if self.approval_request_id:
            template = self.sudo().template_id
            if template and template.template_module == 'hr' and self.replaced_content:
                addressed_to_text = self.approval_request_id.sudo().addressed_to if self.approval_request_id.addressed_to else 'N/A'
                self.replaced_content = self.replaced_content.replace('*Addressed To*', addressed_to_text)

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
            self.status = 'pending'
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

    @api.depends('status', 'delivery_method', 'approval_request_id')
    def _compute_can_download(self):
        user = self.env.user
        for record in self:
            record.can_download = False  # default to hidden

            # No approval request? never downloadable
            if not record.approval_request_id:
                continue

            owner_user = record.approval_request_id.request_owner_id if record.approval_request_id.request_owner_id else None
            # Request owner: can download only digital letters and when issued/downloaded
            if owner_user == user:
                if record.status in ('issued', 'downloaded') and record.delivery_method == 'digital':
                    record.can_download = True
            else:
                # HR/Manager approvers: can download if status is issued/downloaded
                if record.status in ('issued', 'downloaded'):
                    record.can_download = True

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
        self = self.sudo()
        self.letter_id.approval_request_id.action_draft()
        self.letter_id.status = 'draft'
        self.letter_id.approval_request_id = None
        return {
            'type': 'ir.actions.act_window',
            'name': 'Letters',
            'res_model': 'letter.letter',
            'view_mode': 'list,form',
            'target': 'current',
        }

    def action_cancel(self):
        """User clicked No"""
        return {'type': 'ir.actions.act_window_close'}
