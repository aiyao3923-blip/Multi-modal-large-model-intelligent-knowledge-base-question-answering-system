"""Academic labels are opt-in metadata, not deletion or rewritten evidence."""
import copy
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.engines.academic_retrieval import annotate_chunks, annotate_for_kb, low_information
from app.engines.doc_parser import Part

BODY = 'Our method uses two satellite observations to detect mining changes and preserve semantic consistency.'
REF = '[1] Other Authors. An earlier change detection method. Journal of Remote Sensing, 2020.'


def c(text, page=1):
    return {'content':text, 'page_no':page, 'meta':{'embedding_model_id':'kept-identity'}}

class AcademicRetrievalTests(unittest.TestCase):
    def test_midpage_reference_does_not_remove_body(self):
        parts=[Part(text=BODY+'\nReferences\n'+REF,page_no=1)]
        labels=annotate_chunks(parts,[c(BODY),c(REF)],'pdf')
        self.assertFalse(labels[0]['retrieval_excluded'])
        self.assertEqual(labels[1]['retrieval_reason'],'bibliography')

    def test_reference_continues_until_appendix(self):
        appendix='Additional experiments verify the proposed method under realistic registration errors.'
        parts=[Part(text='VII. REFERENCES\n'+REF,page_no=1),Part(text=REF,page_no=2),
               Part(text='Appendix A. Additional results\n'+appendix,page_no=3)]
        labels=annotate_chunks(parts,[c(REF),c(REF,2),c(appendix,3)],'pdf')
        self.assertEqual([v['retrieval_excluded'] for v in labels],[True,True,False])

    def test_body_word_references_is_not_a_section(self):
        text='Our references include several datasets, but this sentence describes the main experiments.'
        label=annotate_chunks([Part(text=text,page_no=1)],[c(text)],'pdf')[0]
        self.assertFalse(label['retrieval_excluded'])

    def test_repeated_text_is_located_in_reading_order(self):
        text=BODY+'\nReferences\n'+BODY
        labels=annotate_chunks([Part(text=text,page_no=1)],[c(BODY),c(BODY)],'pdf')
        self.assertFalse(labels[0]['retrieval_excluded'])
        self.assertTrue(labels[1]['retrieval_excluded'])

    def test_mixed_chunk_keeps_majority_scientific_body(self):
        content=BODY+'\nReferences\n[1] Prior work.'
        label=annotate_chunks([Part(text=content,page_no=1)],[c(content)],'pdf')[0]
        self.assertFalse(label['retrieval_excluded'])

    def test_unknown_location_in_mixed_page_is_conservatively_kept(self):
        parts=[Part(text=BODY+'\nReferences\n'+REF,page_no=1)]
        label=annotate_chunks(parts,[c('A meaningful unmatched scientific statement about the local student model.')],'pdf')[0]
        self.assertFalse(label['retrieval_excluded'])

    def test_no_references_state_leaks_between_documents(self):
        annotate_chunks([Part(text='References\n'+REF,page_no=1)],[c(REF)],'pdf')
        label=annotate_chunks([Part(text=BODY,page_no=1)],[c(BODY)],'pdf')[0]
        self.assertFalse(label['retrieval_excluded'])

    def test_low_information_and_contact_only(self):
        for text in ['?','CFRL: 4669.2;','author@example.org;','https://example.org/repo','x+y=0']:
            with self.subTest(text=text):self.assertIsNotNone(low_information(text))
        for text in ['许可仍待补证','High resolution is available during training only.', BODY]:
            with self.subTest(text=text):self.assertIsNone(low_information(text))

    def test_markdown_research_reference_discussion_is_kept(self):
        text='References\n'+BODY
        label=annotate_chunks([Part(text=text,page_no=0)],[c(text,0)],'md')[0]
        self.assertFalse(label['retrieval_excluded'])

    def test_source_header_is_not_paper_evidence(self):
        text='> ID: "P001"\n> source SHA-256: "'+'a'*64+'"\n> 来源相对路径: 论文原件'
        label=annotate_chunks([], [c(text,0)], 'md')[0]
        self.assertEqual(label['retrieval_reason'],'source_metadata')

    def test_original_chunks_and_identity_are_not_rewritten(self):
        chunks=[c(BODY)];original=copy.deepcopy(chunks)
        label=annotate_chunks([Part(text=BODY,page_no=1)],chunks,'pdf')[0]
        self.assertEqual(chunks,original)
        self.assertEqual(label['embedding_model_id'],'kept-identity')

    def test_disabled_profile_is_identical(self):
        chunks=[c('?')];original=copy.deepcopy(chunks)
        with patch('app.engines.academic_retrieval.profile_enabled',return_value=False):
            self.assertIs(annotate_for_kb(1,[],chunks,'pdf'),chunks)
        self.assertEqual(chunks,original)

    def test_letter_prefixed_appendix_is_kept_and_checklist_is_not_evidence(self):
        checklist='Authors should provide information about experiment reproducibility and societal impact.'
        parts=[Part(text='References\n'+REF,page_no=1),
               Part(text='A Appendix / supplemental material\n'+BODY,page_no=2),
               Part(text='NeurIPS Paper Checklist\n'+checklist,page_no=3)]
        labels=annotate_chunks(parts,[c(REF),c(BODY,2),c(checklist,3)],'pdf')
        self.assertTrue(labels[0]['retrieval_excluded'])
        self.assertFalse(labels[1]['retrieval_excluded'])
        self.assertEqual(labels[2]['retrieval_reason'],'reporting_checklist')
if __name__=='__main__':unittest.main()