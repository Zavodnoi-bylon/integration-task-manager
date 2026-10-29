import unittest
from activity.asana_completion import completion_match


class AsanaCompletionTests(unittest.TestCase):
    def assignment(self, title='Brand | IN | 30$ | 583', naming=None, variants=''):
        return f'Вам назначена задача\n{title}\n\nОписание:\nНейминг - {naming or title}\n{variants}'

    def comment(self, naming, title='Brand | IN | 30$ | 583'):
        return f'Новый комментарий\n{title}\n\n{naming}'

    def test_description_naming_takes_precedence_over_operational_title_suffix(self):
        result = completion_match(self.assignment('Brand | BR | 30$ match_land', 'Brand | BR | 30$'),
                                  self.comment('044038\nBR\nBrand | BR | 30$'))
        self.assertTrue(result['matched'])
        self.assertEqual(result['destination_ids'], ['044038'])

    def test_all_explicit_landing_variants_complete_one_task(self):
        assignment = self.assignment(variants='Wheel - https://example.org/w\nREG - https://example.org/r')
        wheel = '044696\nIN\nBrand | IN | 30$ | 583 | Wheel'
        reg = '044697\nIN\nBrand | IN | 30$ | 583 | REG'
        result = completion_match(assignment, self.comment(wheel+'\n\n'+reg))
        self.assertTrue(result['matched'])
        self.assertEqual(result['destination_ids'], ['044696', '044697'])
        self.assertFalse(completion_match(assignment, self.comment(wheel))['matched'])
        self.assertFalse(completion_match(assignment, self.comment(
            'Brand | IN | 30$ | 583 | Wheel\nBrand | IN | 30$ | 583 | REG'))['matched'])

    def test_heading_alone_is_not_completion(self):
        self.assertFalse(completion_match(self.assignment(), self.comment('Проверьте настройки'))['matched'])

    def test_meaningful_suffix_geo_and_rate_must_match(self):
        for naming in ['Brand | IN | 30$ | 578', 'Brand | BR | 30$ | 583',
                       'Brand | IN | 33$ | 583', 'Brand | IN | 30$ | 583 | NEW_acc',
                       'Brand | IN | 30$ | 583 | Wheel']:
            with self.subTest(naming=naming):
                self.assertFalse(completion_match(self.assignment(), self.comment(naming))['matched'])


if __name__ == '__main__':
    unittest.main()
