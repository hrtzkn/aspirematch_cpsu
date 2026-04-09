letter_descriptions = {
        "A": "Arts, A/V Technology and Communications: Interest in creative or performing arts, communication or A/V technology.",
        "B": "Science, Technology, Engineering and Mathematics: Interest in problem-solving, analyzing and applying scientific knowledge.",
        "C": "Plants, Agriculture and Natural Resources: Interest in outdoor activities involving plants and nature.",
        "D": "Law, Public Safety, Corrections and Security: Interest in legal and protective services for people and property.",
        "E": "Mechanical Manufacturing: Interest in applying mechanical principles using machines and tools.",
        "F": "Industrial Manufacturing: Interest in structured activities in a factory or industrial setting.",
        "G": "Business, Management and Administration: Interest in business organization and leadership.",
        "H": "Marketing, Sales and Service: Interest in persuasion and promotional techniques.",
        "I": "Hospitality and Tourism: Interest in travel planning, hotels, restaurants, and recreation.",
        "J": "Human Services: Interest in helping others with mental, social, or career needs.",
        "K": "Government and Public Administration: Interest in working in government functions.",
        "L": "Architecture, Design and Construction: Interest in planning, designing, and building structures.",
        "M": "Education and Training: Interest in teaching, training, and managing educational services.",
        "N": "Finance, Banking, Investments and Insurance: Interest in financial planning and banking services.",
        "O": "Health Sciences, Care and Prevention: Interest in healthcare and medical research.",
        "P": "Information Technology (IT): Interest in computer systems, software, and tech support.",
        "Q": "Animals, Agriculture and Natural Resources: Interest in working with and caring for animals.",
        "R": "Transportation, Distribution and Logistics: Interest in transportation and supply chain management."
    }

short_letter_descriptions = { 
    "A": "Arts",
    "B": "STEM",
    "C": "Agriculture",
    "D": "Law",
    "E": "Mechanical",
    "F": "Industrial",
    "G": "Business",
    "H": "Marketing",
    "I": "Tourism",
    "J": "HumanServices",
    "K": "Government",
    "L": "Architecture",
    "M": "Education",
    "N": "Finance",
    "O": "Health",
    "P": "IT",
    "Q": "Animals",
    "R": "Transport"
}

preferred_program_map = {
    "HM": ["G", "I"],
    "AGRI": ["C", "Q"],
    "EDUC": ["M"],
    "IT": ["A", "B", "P"],
    "CRIM": ["D"]
}

import random

ai_responses = {
    "A": [
        "That shows you're into creative and technical work.",
        "You enjoy hands-on creative tasks.",
        "Looks like you love blending creativity with technology!"
    ],
    "B": [
        "You seem to enjoy science and solving problems!",
        "You like analyzing and figuring things out.",
        "You’re definitely into exploration and scientific thinking."
    ],
    "C": [
        "You really enjoy nature and outdoor activities!",
        "You like working with plants and the environment.",
        "Looks like you're happiest doing hands-on outdoor tasks."
    ],
    "D": [
        "You’re great at expressing ideas and writing!",
        "You seem to enjoy communication and detailed tasks.",
        "You definitely have a talent for organizing thoughts into writing."
    ],
    "E": [
        "You enjoy analyzing things and making sense of information!",
        "Looks like you're someone who enjoys structured tasks.",
        "Numbers and details seem to fit your style!"
    ],
    "F": [
        "You enjoy hands-on tasks and working with equipment!",
        "You like operating machines and practical work.",
        "You're very mechanically inclined!"
    ],
    "G": [
        "You prefer organized office or business environments!",
        "You’re comfortable in structured, professional settings.",
        "You like organization, planning, and clear workflows."
    ],
    "H": [
        "You're great with people — talking and helping customers!",
        "You enjoy interacting and connecting with others.",
        "Looks like you're friendly and service-oriented!"
    ],
    "I": [
        "You enjoy hospitality and helping people feel welcome!",
        "You like travel, tourism, and creating good experiences.",
        "You’re drawn to hospitality and event-related tasks!"
    ],
    "J": [
        "You love helping and supporting others!",
        "You’re someone who cares deeply about people.",
        "You enjoy guiding and assisting others emotionally or socially."
    ],
    "K": [
        "You seem drawn to public service and government work!",
        "You like structure, rules, and helping communities.",
        "You enjoy roles that involve responsibility and leadership."
    ],
    "L": [
        "You enjoy planning, designing, or building things!",
        "You're creative and technical — a great combo!",
        "You’re into design and shaping environments."
    ],
    "M": [
        "You enjoy teaching and helping people learn!",
        "You're patient and great at guiding others.",
        "You have a talent for sharing knowledge."
    ],
    "N": [
        "You like working with numbers and planning!",
        "Finance or business seems to match your style.",
        "You're organized and detail-oriented about money matters."
    ],
    "O": [
        "You seem interested in health and helping people stay well!",
        "You care about wellness and medical assistance.",
        "You're drawn to healthcare and service."
    ],
    "P": [
        "You’re definitely into computers and technology!",
        "You enjoy learning how things work digitally.",
        "You're tech-minded and curious."
    ],
    "Q": [
        "You enjoy animals and caring for them!",
        "You love working with pets or wildlife.",
        "You're drawn to nature and animal care."
    ],
    "R": [
        "You’re interested in transportation and logistics!",
        "You like organizing movement and deliveries.",
        "You enjoy tasks involving travel and coordination."
    ]
}

