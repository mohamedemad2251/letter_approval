{
    'name' : 'Letter Writer - Approvals',
    'description' : ''' "Letter Writer's Extension. This module extends HR even further by requiring the Approvals module. Which then adds functionality for Letter Approval which automates Letter Writer even further."
                    ''',
    'depends' : ['base','letter_writer','letter_hr','approvals'],
    'version' : '1.0',
    'author' : 'Mohamed Emad',
    'data' : [
        'security/override_security.xml',
        'security/ir.model.access.csv',
        'security/record_rules_security.xml',
        'data/letter_placeholders_approval.xml',
        'report/report.xml',
        'views/confirmation_wizard_view.xml',
        'views/letter_views.xml',
        'views/approval_views.xml',
        'data/approval_category_data.xml',
    ],
    'installable' : True,
}
