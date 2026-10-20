import unittest

from app.services import infer_category


class CategoryClassificationTests(unittest.TestCase):
    def test_existing_category_taxonomy_covers_recognizable_multilingual_issues(self):
        examples = (
            ("A large pothole has formed on the road", "Roads"),
            ("Poor road condition near the bus stop", "Roads"),
            ("Bad road condition on this street", "Roads"),
            ("Roads are in bad condition", "Roads"),
            ("रस्त्याची अवस्था खराब आहे", "Roads"),
            ("सड़क की हालत खराब है", "Roads"),
            ("sadak ki halat kharab hai", "Roads"),
            ("rastyachi durdasha zali aahe", "Roads"),
            ("रस्त्यावर मोठा खड्डा पडला आहे", "Roads"),
            ("सड़क किनारे कूड़ा जमा है", "Solid Waste Management (SWM)"),
            ("पदपथावर अतिक्रमण झाले आहे", "Encroachment"),
            ("The footpath is occupied by illegal food stalls", "Encroachment"),
            ("पाण्याची गळती होत आहे", "Water Supply"),
            ("Food poisoning reported after unsafe food was sold", "Public health"),
            ("Unsafe food is being sold by a street stall", "Public health"),
            ("भेसळयुक्त अन्न विकले जात आहे", "Public health"),
            ("मिलावटी खाना बेचा जा रहा है", "Public health"),
            ("Traffic jam due to a blocked drain", "Drainage"),
            ("Traffic light is not working at JJ signal", "Transport and mobility"),
            ("A traffic signal is broken", "Transport and mobility"),
            ("ट्रॅफिक सिग्नल काम करत नाही", "Transport and mobility"),
            ("Illegal hawkers are blocking the footpath", "Encroachment"),
            ("अनधिकृत फेरीवाले पदपथ अडवत आहेत", "Encroachment"),
            ("Illegal parking is blocking the entrance and footpath", "Encroachment"),
            ("बेकायदेशीर पार्किंग केल्यामुळे प्रवेशद्वार आणि पदपथ अडवले गेले आहेत", "Encroachment"),
            ("अस्पताल के बाहर अवैध पार्किंग से फुटपाथ बंद है", "Encroachment"),
        )
        for text, expected in examples:
            with self.subTest(text=text):
                self.assertEqual(infer_category(text), expected)

    def test_ambiguous_complaint_remains_unclassified(self):
        self.assertIsNone(infer_category("Please look into this issue soon"))

    def test_prompt_acceptance_fixtures_use_existing_categories_only(self):
        examples = (
            ("Thadomal Shahani College जवळ अवैध खाद्य स्टॉल आणि अन्नातून विषबाधा", "Public health"),
            ("DJ Sanghvi College in Vile Parle has a poor road condition", "Roads"),
            ("शहा अँकर कॉलेज चेंबूर बाहेर मोठा खड्डा", "Roads"),
            ("Footpath encroachment outside Fatima High School in Vidyavihar West", "Encroachment"),
            ("Garbage collection outside Nausena Vihar in Vidyavihar West", "Solid Waste Management (SWM)"),
            ("Poor road condition near Mittal College in Malad West", "Roads"),
            ("Footpath encroachment outside Mittal College in Malad West", "Encroachment"),
        )
        for text, expected in examples:
            with self.subTest(text=text):
                self.assertEqual(infer_category(text), expected)


if __name__ == "__main__":
    unittest.main()
