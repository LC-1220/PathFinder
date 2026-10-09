/* ocr */
let students = [];
let selectedStudent = null;
let configuredAcademicStrands = ['STEM', 'ABM', 'HUMSS', 'GAS', 'TVL', 'ICT', 'SPORTS', 'ARTS_DESIGN'];
let configuredRecommendationLimit = 3;
 

function addStudent() {
    const getVal = id => document.getElementById(id) ? document.getElementById(id).value : '';
    let firstName = getVal("firstName");
    let middleInitial = getVal("middleInitial");
    let lastName = getVal("lastName");
    let studentNumber = getVal("studentNumber");
    let course = getVal("course");
    let academicRecord = getVal("age");
 
    if (
        firstName.trim() === "" ||
        lastName.trim() === "" ||
        studentNumber.trim() === ""
    ) {
        alert("Please fill in all required fields.");
        return;
    }
 
    let student = {
        firstName: firstName,
        middleInitial: middleInitial,
        lastName: lastName,
        studentNumber: studentNumber,
        course: course,
        academicRecord: academicRecord,
        gwa: '',
        grades: '',
        subjects: ''
    };
 
                list.querySelectorAll('.inline-recommendation-card').forEach(card => card.classList.remove('selected'));
                activeCard?.classList.add('selected');
    students.push(student);
 
    renderStudents();
 
    clearInputs();
 
    alert("Student Added Successfully!");
}

function parseStudent(text){
    if(!text){
        console.warn('parseStudent: no text provided');
        return null;
    }

    const normalizeGradeValue = raw => {
        if(raw === null || raw === undefined || raw === '') return null;
        const cleaned = String(raw).trim();
        if(!cleaned) return null;

        const letterMap = {
            'A+': 97, 'A': 95, 'A-': 92,
            'B+': 89, 'B': 85, 'B-': 82,
            'C+': 79, 'C': 75, 'C-': 72,
            'D+': 69, 'D': 65, 'D-': 62,
            'F': 50
        };

        const upper = cleaned.toUpperCase();
        if(letterMap[upper]) return letterMap[upper];

        const num = Number.parseFloat(cleaned.replace(/[^0-9.\-]/g, ''));
        if(Number.isFinite(num)){
            if(num >= 0 && num <= 5) return num * 20;
            if(num >= 1 && num <= 100) return num;
            if(num >= 100 && num <= 500) return num / 5;
        }

        return null;
    };

    const normalizeText = raw => raw
        .replace(/\r/g, '\n')
        .replace(/\u00A0/g, ' ')
        .replace(/\t+/g, ' ')
        .replace(/-\n/g, '')
        .replace(/\n{2,}/g, '\n')
        .replace(/ {2,}/g, ' ')
        .trim();

    const validateGrade = rawValue => {
        const original = String(rawValue ?? '').trim();
        if(!original) return { normalized: null, status: 'INVALID', reason: 'missing grade value' };

        const cleaned = original
            .replace(/[O]/gi, '0')
            .replace(/\s+/g, '')
            .replace(/[^0-9.\-]/g, '');

        if(!cleaned || cleaned === '-' || cleaned === '.'){
            return { normalized: null, status: 'REVIEW', reason: 'grade OCR is unreadable' };
        }

        const normalized = cleaned.replace(/^(\d+)\.(\d*)\.(\d+)$/, '$1.$2$3').replace(/\.(?=\.)/g, '');
        const num = Number.parseFloat(normalized);
        if(Number.isNaN(num)){
            return { normalized: null, status: 'REVIEW', reason: 'could not parse a numeric grade', raw: original };
        }

        const safeNum = Number(num.toFixed(2));
        if(safeNum >= 75 && safeNum <= 99){
            return { normalized: safeNum, status: 'VALID', reason: 'within valid school range', raw: original };
        }

        return {
            normalized: safeNum,
            status: 'INVALID',
            reason: safeNum < 75 ? 'below passing threshold (75â€“99 expected)' : 'above expected school grade range',
            raw: original,
        };
    };

    const parseTableRow = line => {
        if(!line) return null;
        const trimmed = line.trim();
        if(!trimmed || /subject\s*code|subject\s*name|credit|instructor|grade|remarks|student\s*id|student\s*name|course|major|year|term|school\s*year|gwa|gpa|passed|failed/i.test(trimmed)){
            return null;
        }

        const rowPatterns = [
            /^\s*(\d{3,6}\s*[A-Z]{1,10})\s+(.+?)\s+(\d{1,3}(?:\.\d+)?)\s+([A-Z][A-Za-z\s.,'\-]+?)\s+(\d{1,3}(?:\.\d+)?)\s*(?:Passed|Failed|Incomplete|Conditional|Withdrawn|Remarks)?\s*$/i,
            /^\s*(\d{3,6}\s*[A-Z]{1,10})\s+(.+?)\s+(\d{1,3}(?:\.\d+)?)\s+([A-Z][A-Za-z\s.,'\-]+?)\s+(\d{1,3}(?:\.\d+)?)\s*(.*)$/i,
            /^\s*(\d{3,6}\s*[A-Z]{1,10})\s+(.+?)\s+(\d{1,3}(?:\.\d+)?)\s+([A-Z][A-Za-z\s.,'\-]+?)\s+([A-Z0-9.]+)\s*(.*)$/i,
        ];

        for(const pattern of rowPatterns){
            const match = trimmed.match(pattern);
            if(!match) continue;

            const subjectCode = match[1].trim();
            const subjectName = match[2].trim();
            const credit = match[3].trim();
            const instructor = match[4].trim();
            const gradeToken = match[5].trim();
            const remarks = (match[6] || '').trim();
            const gradeCheck = validateGrade(gradeToken);

            if(!subjectCode || !subjectName || !instructor || !gradeCheck.normalized){
                continue;
            }

            return {
                subject_code: subjectCode,
                subject_name: subjectName,
                credit: Number.parseFloat(credit) || credit,
                instructor,
                grade: gradeCheck.normalized,
                remarks: remarks || 'Passed',
                grade_status: gradeCheck.status,
                grade_reason: gradeCheck.reason,
            };
        }

        return null;
    };

    const prepareForVerification = text => {
        const normalized = normalizeText(text);
        return normalized
            .split(/\n+/)
            .map(line => parseTableRow(line))
            .filter(Boolean);
    };

    const subjectCatalog = {
        '11101': 'Oral Communication in Context',
        'ENG1': 'Oral Communication in Context',
        '11101 ENG1': 'Oral Communication in Context',
        '11102': 'Komunikasyon at Pananaliksik sa Wika at Kulturang Pilipino',
        'FIL1': 'Komunikasyon at Pananaliksik sa Wika at Kulturang Pilipino',
        '11102 FIL1': 'Komunikasyon at Pananaliksik sa Wika at Kulturang Pilipino',
        '11103': 'General Mathematics',
        'MAT1': 'General Mathematics',
        '11103 MAT1': 'General Mathematics',
        '11104': 'Earth and Life Science',
        'ESC': 'Earth and Life Science',
        '11104 ESC': 'Earth and Life Science',
        '11105': 'Introduction to the Philosophy of the Human Person',
        'IPP': 'Introduction to the Philosophy of the Human Person',
        '11105 IPP': 'Introduction to the Philosophy of the Human Person',
        '11106': 'Physical Education and Health 1',
        'PE1': 'Physical Education and Health 1',
        '11106 PE1': 'Physical Education and Health 1',
        '11107': 'Empowerment Technologies',
        'ET': 'Empowerment Technologies',
        '11107 ET': 'Empowerment Technologies',
        '11108': 'Filipino Christian Living 1',
        'FCL1': 'Filipino Christian Living 1',
        '11108 FCL1': 'Filipino Christian Living 1',
        '11114': 'Tour Guiding Services 1',
        'TG': 'Tour Guiding Services 1',
        '11114 TG': 'Tour Guiding Services 1',
        '11115': 'Nihongo 1',
        'NHG1': 'Nihongo 1',
        '11115 NHG1': 'Nihongo 1',
        '12160': 'Bread and Pastry Production 1',
        'BAP': 'Bread and Pastry Production 1',
        '12160 BAP': 'Bread and Pastry Production 1',
        '218': 'Reading and Writing Skills',
        'ENG2': 'Reading and Writing Skills',
        '218 ENG2': 'Reading and Writing Skills',
        '220': 'Statistics and Probability',
        'STAT': 'Statistics and Probability',
        '220 STAT': 'Statistics and Probability',
        '221': 'Physical Science',
        'PSC': 'Physical Science',
        '221 PSC': 'Physical Science',
        '223': 'Physical Education and Health 2',
        'PE2': 'Physical Education and Health 2',
        '223 PE2': 'Physical Education and Health 2',
        '235': 'Tour Guiding Services 2',
        '235 TG': 'Tour Guiding Services 2',
        '239': 'Nihongo 2',
        'NHG2': 'Nihongo 2',
        '239 NHG2': 'Nihongo 2',
        '288': 'Bread and Pastry Production 2',
        '288 BAP': 'Bread and Pastry Production 2',
        '219': 'Pagbasa at Pagsusuri ng Iba\'t Ibang Teksto Tungo sa Pananaliksik',
        'FIL2': 'Pagbasa at Pagsusuri ng Iba\'t Ibang Teksto Tungo sa Pananaliksik',
        '219 FIL2': 'Pagbasa at Pagsusuri ng Iba\'t Ibang Teksto Tungo sa Pananaliksik',
        '222': 'Personal Development',
        'PDV': 'Personal Development',
        '222 PDV': 'Personal Development',
        '224': 'Practical Research 1: Qualitative',
        'RES1': 'Practical Research 1: Qualitative',
        '224 RES1': 'Practical Research 1: Qualitative',
        '225': 'Filipino Christian Living 2',
        'FCL2': 'Filipino Christian Living 2',
        '225 FCL2': 'Filipino Christian Living 2',
        '11236': 'Tourism Production Services 1',
        'TPS': 'Tourism Production Services 1',
        '11236 TPS': 'Tourism Production Services 1',
        '12140': '21st Century Literature from the Philippines and the World',
        'CLP': '21st Century Literature from the Philippines and the World',
        '12140 CLP': '21st Century Literature from the Philippines and the World',
        '12143': 'Understanding Culture, Society and Politics',
        'UCS': 'Understanding Culture, Society and Politics',
        '12143 UCS': 'Understanding Culture, Society and Politics',
        '12144': 'Physical Education and Health 3',
        'PE3': 'Physical Education and Health 3',
        '12144 PE3': 'Physical Education and Health 3',
        '12145': 'English for Academic and Professional Purposes',
        'EAP': 'English for Academic and Professional Purposes',
        '12145 EAP': 'English for Academic and Professional Purposes',
        '12146': 'Pagsulat sa Filipino sa Piling Larangan',
        'FIL3': 'Pagsulat sa Filipino sa Piling Larangan',
        '12146 FIL3': 'Pagsulat sa Filipino sa Piling Larangan',
        '12147': 'Filipino Christian Living 3',
        'FCL3': 'Filipino Christian Living 3',
        '12147 FCL3': 'Filipino Christian Living 3',
        '12163': 'Nihongo 3',
        'NHG3': 'Nihongo 3',
        '12163 NHG3': 'Nihongo 3',
        '12265': 'Practical Research 2: Quantitative',
        'RES2': 'Practical Research 2: Quantitative',
        '12265 RES2': 'Practical Research 2: Quantitative',
        '12266': 'Entrepreneurship',
        'ENT': 'Entrepreneurship',
        '12266 ENT': 'Entrepreneurship',
        '12289': 'Food and Beverage Services 1',
        'FBS': 'Food and Beverage Services 1',
        '12289 FBS': 'Food and Beverage Services 1'
    };

    const normalizeSubjectName = raw => {
        if(raw === null || raw === undefined || raw === '') return '';
        let text = String(raw).trim().replace(/\s{2,}/g, ' ');
        if(!text) return '';

        text = text.replace(/^(?:\d{4,6}\s+)?[A-Z0-9]{2,10}\s+/i, '');
        text = text.replace(/\s*[\-â€“:|/]+\s*\d{1,3}(?:\.\d+)?\s*$/, '');
        text = text.replace(/\s+\d{1,3}(?:\.\d+)?\s*$/, '');
        text = text.replace(/^[-:|/]+\s*/, '').trim();
        text = text.replace(/\s+[-:|/]+\s*$/, '').trim();
        text = text.replace(/\s{2,}/g, ' ').trim();

        return text;
    };

    const resolveSubjectLabel = label => {
        const text = normalizeSubjectName(label);
        if(!text) return '';

        const direct = subjectCatalog[text.toUpperCase()] || subjectCatalog[text.replace(/\s+/g, ' ').toUpperCase()];
        if(direct) return direct;

        const codeMatch = text.match(/(\d{4,6})\s*([A-Z0-9]{2,10})?/i);
        if(codeMatch){
            const code = codeMatch[1];
            const codeKey = codeMatch[2] ? `${code} ${codeMatch[2]}`.toUpperCase() : code;
            const mapped = subjectCatalog[codeKey] || subjectCatalog[code];
            if(mapped) return mapped;
        }

        const shortMatch = text.match(/\b([A-Z]{2,10})\b/i);
        if(shortMatch){
            const mapped = subjectCatalog[shortMatch[1].toUpperCase()];
            if(mapped) return mapped;
        }

        return text;
    };

    const normalized = normalizeText(text);
    const lines = normalized.split(/\n+/).map(l => l.trim()).filter(Boolean);

    const compactLayout = (() => {
        const compactNumber = lines.findIndex(line => /^\s*(?:S\s*)?\d{6,9}(?:-\d+)?\s*$/i.test(line.trim()));
        if(compactNumber < 0) return null;

        const subjectGradePattern = /^([A-Z]{2,10})\s+(\d{1,3}(?:\.\d+)?)\s*$/i;
        const subjectRows = [];
        for(let i = 0; i < lines.length; i++){
            const trimmed = lines[i].trim();
            const match = trimmed.match(subjectGradePattern);
            if(!match) continue;
            const grade = Number.parseFloat(match[2]);
            if(Number.isFinite(grade) && grade >= 0 && grade <= 100){
                const resolvedLabel = resolveSubjectLabel(match[1]) || match[1].toUpperCase();
                subjectRows.push({ label: resolvedLabel, grade });
            }
        }

        if(subjectRows.length === 0) return null;

        const candidateWindow = lines.slice(compactNumber + 1);
        const compactName = candidateWindow.find(line => {
            const trimmed = line.trim();
            if(!trimmed || /\b(?:student|number|course|subjects?|grade|year|gwa|gpa|major|program|track|section)\b/i.test(trimmed)) return false;
            if(subjectGradePattern.test(trimmed)) return false;
            const tokens = trimmed.split(/\s+/).filter(Boolean);
            return tokens.length >= 2 && tokens.length <= 5 && /^[A-Z][A-Za-z\s\-\.']+$/.test(trimmed) && !/\d/.test(trimmed);
        });
        if(!compactName) return null;

        const compactCourse = candidateWindow.find(line => {
            const trimmed = line.trim();
            if(!trimmed || /\b(?:student|number|name|grade|subject|gwa|gpa|year|semester|term|school|page)\b/i.test(trimmed)) return false;
            if(subjectGradePattern.test(trimmed)) return false;
            return /^[A-Z0-9]+(?:[-/][A-Z0-9]+)+$/.test(trimmed) || /^[A-Z]{2,10}\s*[-/]?\s*[A-Z0-9]{2,10}$/.test(trimmed);
        });
        if(!compactCourse) return null;

        const uniqueSubjects = [];
        const seen = new Set();
        for(const item of subjectRows){
            const key = `${item.label}|${item.grade.toFixed(2)}`;
            if(seen.has(key)) continue;
            seen.add(key);
            uniqueSubjects.push(item);
        }

        return {
            studentNumber: lines[compactNumber].trim().replace(/\s+/g, '').toUpperCase(),
            studentName: compactName.trim(),
            course: compactCourse.trim(),
            subjects: uniqueSubjects
        };
    })();

    const tableLayout = (() => {
        const idLine = lines.find(line => /student\s*id\s*[:\-]?\s*(.+)/i.test(line));
        const nameLine = lines.find(line => /student\s*name\s*[:\-]?\s*(.+)/i.test(line));
        const courseLine = lines.find(line => /course\s*(?:\/\s*major)?\s*[:\-]?\s*(.+)/i.test(line));
        if(!idLine || !nameLine || !courseLine) return null;

        const subjects = [];
        for(const line of lines){
            const trimmed = line.trim();
            if(!trimmed || /subject\s*code|subject\s*name|credit|instructor|grade|remarks|show|prelim|year|student\s*id|student\s*name|course\s*(?:\/\s*major)?|passed|failed/i.test(trimmed)) continue;

            const rowMatch = trimmed.match(/^(\d{4,6})\s+[A-Z]{2,10}\s+(.+?)\s+\d{1,3}(?:\.\d+)?\s+[A-Z][A-Za-z\s.,\-']*\s*,?\s*[A-Z][A-Za-z\s.,\-']*\s+(\d{1,3}(?:\.\d+)?)\s*(?:Passed|Failed|Incomplete|Conditional|Remarks|Approved)?$/i);
            if(!rowMatch) continue;

            const label = resolveSubjectLabel(rowMatch[2].trim().replace(/\s{2,}/g, ' '));
            const grade = Number.parseFloat(rowMatch[3]);
            if(label && Number.isFinite(grade) && grade >= 0 && grade <= 100){
                subjects.push({ label, grade });
            }
        }

        if(subjects.length === 0) return null;

        const studentNumber = String(idLine.match(/student\s*id\s*[:\-]?\s*(.+)/i)?.[1] || '').trim();
        const studentName = String(nameLine.match(/student\s*name\s*[:\-]?\s*(.+)/i)?.[1] || '').trim();
        const course = String(courseLine.match(/course\s*(?:\/\s*major)?\s*[:\-]?\s*(.+)/i)?.[1] || '').trim();

        return {
            studentNumber: studentNumber || '',
            studentName,
            course,
            subjects
        };
    })();

    const findValue = patterns => {
        for(const line of lines){
            for(const pattern of patterns){
                const match = line.match(pattern);
                if(match && match[1]) return match[1].trim();
            }
        }
        return '';
    };

    const extractExplicitValue = regexes => {
        for(const line of lines){
            for(const re of regexes){
                const match = line.match(re);
                if(match){
                    return match[1] ? match[1].trim() : line.replace(re, '').trim();
                }
            }
        }
        return '';
    };

    const findLine = pattern => lines.find(l => pattern.test(l)) || '';

    const parseStudentNumber = raw => {
        const value = String(raw || '').trim();
        if(!value) return '';

        const clean = value
            .replace(/^student\s*(?:number|no|id|#)\s*[:\-]?\s*/i, '')
            .replace(/^student\s*[:\-]?\s*/i, '')
            .replace(/\s+/g, '')
            .trim();

        if(!clean) return '';

        const normalized = clean.toUpperCase().replace(/O/g, '0').replace(/I/g, '1').replace(/L/g, '1');
        const numMatch = normalized.match(/(?:S)?\d{6,9}(?:-\d+)?/i);
        if(numMatch) return numMatch[0].replace(/\s+/g, '').toUpperCase();

        const digits = normalized.replace(/[^0-9]/g, '');
        if(digits.length === 9) return `${digits.slice(0,2)}-${digits.slice(2,6)}-${digits.slice(6)}`;
        if(digits.length >= 6) return normalized.replace(/[^A-Z0-9-]/g, '');
        return normalized.replace(/[^A-Z0-9-]/g, '');
    };

    const parseName = raw => {
        const value = (raw || '').replace(/\s{2,}/g, ' ').trim();
        if(!value) return { firstName: '', middleInitial: '', lastName: '' };

        const cleaned = value
            .replace(/^student\s*name\s*[:\-]?\s*/i, '')
            .replace(/^name\s*[:\-]?\s*/i, '')
            .replace(/^student\s*[:\-]?\s*/i, '')
            .replace(/^\s*\([^)]*\)\s*/g, '')
            .replace(/\s+/g, ' ')
            .trim();

        if(!cleaned) return { firstName: '', middleInitial: '', lastName: '' };

        const normalized = cleaned
            .replace(/\s*,\s*/g, ', ')
            .replace(/\b[A-Z]\.[A-Z]\b/g, match => match.replace('.', ''));

        if(normalized.includes(',')){
            const [lastPart, restPart] = normalized.split(',').map(p => p.trim());
            const restParts = (restPart || '').split(/\s+/).filter(Boolean);
            const lastName = lastPart || '';
            if(restParts.length === 0) return { firstName: '', middleInitial: '', lastName };
            if(restParts.length === 1) return { firstName: restParts[0], middleInitial: '', lastName };
            return {
                firstName: restParts[0],
                middleInitial: restParts.slice(1, -1).map(p => p.charAt(0)).join(''),
                lastName
            };
        }

        const parts = normalized.split(/\s+/).filter(Boolean);
        if(parts.length === 1) return { firstName: parts[0], middleInitial: '', lastName: '' };
        if(parts.length === 2) return { firstName: parts[0], middleInitial: '', lastName: parts[1] };

        const firstName = parts[0];
        const lastName = parts[parts.length - 1];
        const middle = parts.slice(1, -1).map(p => p.charAt(0)).join('');
        return { firstName, middleInitial: middle, lastName };
    };

    const metadataLinePattern = /\b(student|year|course|program|strand|track|section|semester|term|school|college|department|name|number|id|adviser|registrar|principal|address|birth|gender|sex|page|curriculum|subject code|units|grade)\b/i;

    const explicitMetadata = {};

    if(lines.length >= 3){
        const numberCandidate = lines.find(line => /^\s*(?:S\s*)?\d{6,9}(?:-\d+)?\s*$/i.test(line.trim()));
        if(numberCandidate){
            explicitMetadata.studentNumber = numberCandidate.trim().replace(/\s+/g, '').toUpperCase();
        }

        const nameCandidate = lines.find(line => {
            const trimmed = line.trim();
            if(!trimmed || /\b(?:student|number|course|subjects?|grade|year|gwa|gpa|major|program|track|section)\b/i.test(trimmed)) return false;
            const tokens = trimmed.split(/\s+/).filter(Boolean);
            return tokens.length >= 2 && tokens.length <= 5 && /^[A-Z][A-Za-z\s\-\.']+$/.test(trimmed) && !/\d/.test(trimmed) && !/^S\d{6,9}$/i.test(trimmed);
        });
        if(nameCandidate){
            explicitMetadata.studentName = nameCandidate.trim();
        }

        const courseCandidate = lines.find(line => {
            const trimmed = line.trim();
            if(!trimmed || /\b(?:student|number|name|grade|subject|gwa|gpa|year|semester|term|school|page)\b/i.test(trimmed)) return false;
            if(/^([A-Z]{2,10})\s+\d{1,3}(?:\.\d+)?$/i.test(trimmed)) return false;
            if(/^([A-Z]{2,10})\s*[-/]\s*\d{1,3}(?:\.\d+)?$/i.test(trimmed)) return false;
            return /^[A-Z0-9]+(?:[-/][A-Z0-9]+)+$/.test(trimmed) || /^[A-Z]{2,10}\s*[-/]?\s*[A-Z0-9]{2,10}$/.test(trimmed);
        });
        if(courseCandidate){
            explicitMetadata.course = courseCandidate.trim();
        }
    }

    const exactStudentNumberLine = lines.find(line => /^\s*(?:S\s*)?\d{6,9}(?:-\d+)?\s*$/i.test(line.trim()));
    if(exactStudentNumberLine){
        explicitMetadata.studentNumber = exactStudentNumberLine.trim().replace(/\s+/g, '').toUpperCase();
    }

    const exactNameLine = lines.find(line => {
        const trimmed = line.trim();
        if(!trimmed || /\b(?:student|number|course|subjects?|grade|year|gwa|gpa|major|program|track|section)\b/i.test(trimmed)) return false;
        const tokenCount = trimmed.split(/\s+/).filter(Boolean).length;
        return tokenCount >= 2 && tokenCount <= 5 && /^[A-Z][A-Za-z\s\-\.']+$/.test(trimmed) && !/\d/.test(trimmed) && !/^S\d{6,9}$/i.test(trimmed);
    });
    if(exactNameLine){
        explicitMetadata.studentName = exactNameLine.trim();
    }

    const exactCourseLine = lines.find(line => {
        const trimmed = line.trim();
        if(!trimmed || /\b(?:student|number|name|grade|subject|gwa|gpa|year|semester|term|school|page)\b/i.test(trimmed)) return false;
        if(/^([A-Z]{2,10})\s+\d{1,3}(?:\.\d+)?$/i.test(trimmed)) return false;
        if(/^([A-Z]{2,10})\s*[-/]\s*\d{1,3}(?:\.\d+)?$/i.test(trimmed)) return false;
        return /^[A-Z0-9]+(?:[-/][A-Z0-9]+)+$/.test(trimmed) || /^[A-Z]{2,10}\s*[-/]?\s*[A-Z0-9]{2,10}$/.test(trimmed);
    });
    if(exactCourseLine){
        explicitMetadata.course = exactCourseLine.trim();
    }

    for(const line of lines){
        const cleaned = line.replace(/\s+/g, ' ').trim();
        const match = cleaned.match(/^(?:student\s*(?:number|no|id)|student\s*name|course(?:\s*\/\s*major)?|major|program|strand|track|year|section|semester|term)\s*[:\-]?\s*(.+)$/i);
        if(!match) continue;
        const key = cleaned.split(/[:\-]/)[0].trim().toLowerCase();
        const value = match[1].trim();
        const canonicalKey = key
            .replace(/^student\s*name$/, 'studentName')
            .replace(/^student\s*(?:number|no|id)$/, 'studentNumber')
            .replace(/^course(?:\s*\/\s*major)?$/, 'course')
            .replace(/^major$/, 'course')
            .replace(/^program$/, 'course')
            .replace(/^strand$/, 'course')
            .replace(/^track$/, 'course')
            .replace(/^year$/, 'year')
            .replace(/^section$/, 'section')
            .replace(/^semester$/, 'semester')
            .replace(/^term$/, 'term');

        if(value && value.length > 0 && (!explicitMetadata[canonicalKey] || explicitMetadata[canonicalKey].length < value.length)){
            explicitMetadata[canonicalKey] = value;
        }
    }

    const parseSubjectRows = () => {
        const rows = lines.slice();
        const subjectEntries = [];
        const gradeRows = [];

        const subjectCodePattern = /^\d{4,6}\s+[A-Z0-9.]{2,}(?:\s+[A-Z0-9.]{2,})*$/i;
        const numberPattern = /\b\d{1,3}(?:\.\d{1,2})?\b/g;
        const subjectNoisePattern = /\b(note|remarks?|credit|instructor|proceed|semester|term|prelim|grade|subject\s*code|subject\s*name)\b/i;
        const subjectKeywordPattern = /\b(math|mathematics|algebra|calculus|statistics|science|biology|chemistry|physics|english|literature|filipino|history|social|economics|technology|computer|ict|programming|accounting|business|management|marketing|psychology|research|values|mapeh|pe|health|arts|esp|physical|education|filipino|tLE|tle|home\s*economics)\b/i;

        const cleanLine = value => (value || '').replace(/\s+/g, ' ').trim();
        const isSubjectCodeLine = line => subjectCodePattern.test(cleanLine(line));
        const isInstructorLine = line => /,/.test(line) && /[A-Za-z]{2,}/.test(line);
        const isSubjectTitleLine = line => {
            if(!line) return false;
            if(isSubjectCodeLine(line)) return false;
            if(isInstructorLine(line)) return false;
            if(subjectNoisePattern.test(line)) return false;
            if(/^passed$|^failed$|^incomplete$/i.test(line)) return false;
            const letters = (line.match(/[A-Za-z]/g) || []).length;
            return letters >= 6;
        };

        const parseNumericTokens = line => {
            const matches = [...line.matchAll(numberPattern)];
            return matches.map(m => ({
                raw: m[0],
                value: parseFloat(m[0]),
                index: m.index
            })).filter(t => Number.isFinite(t.value));
        };

        const normalizeGradeToken = (tokens, gradeIndex) => {
            if(gradeIndex < 0 || gradeIndex >= tokens.length) return null;
            const token = tokens[gradeIndex];
            const prev = gradeIndex > 0 ? tokens[gradeIndex - 1] : null;
            const v = token.value;

            const normalized = normalizeGradeValue(token.raw);
            if(Number.isFinite(normalized) && normalized >= 0 && normalized <= 100){
                return { value: Number(normalized.toFixed(2)), text: String(normalized).replace(/\.0+$/, '').replace(/(\.\d)0+$/, '$1') };
            }

            if(v >= 1 && v <= 5){
                return { value: v, text: token.raw };
            }

            if(v >= 50 && v <= 100){
                return { value: v, text: token.raw };
            }

            const prevLooksLikeUnits = !!prev && Math.abs(prev.value - Math.round(prev.value)) < 0.001 && prev.value >= 1 && prev.value <= 6;

            if(v >= 100 && v <= 500 && prevLooksLikeUnits){
                const normalized = v / 100;
                return { value: normalized, text: normalized.toFixed(2).replace(/\.00$/, '.0') };
            }

            if(v >= 10 && v <= 50 && prevLooksLikeUnits){
                const normalized = v / 10;
                return { value: normalized, text: normalized.toFixed(1) };
            }

            return null;
        };

        const pickGradeTokenIndex = tokens => {
            if(tokens.length === 0) return -1;

            for(let i = tokens.length - 1; i >= 0; i--){
                const v = tokens[i].value;
                if(v >= 50 && v <= 100) return i;
            }

            for(let i = tokens.length - 1; i >= 0; i--){
                const v = tokens[i].value;
                const isDecimal = Math.abs(v - Math.round(v)) > 0.001;
                if(v >= 1 && v <= 5 && isDecimal) return i;
            }

            for(let i = tokens.length - 1; i >= 0; i--){
                const prev = i > 0 ? tokens[i - 1] : null;
                const v = tokens[i].value;
                const prevLooksLikeUnits = !!prev && Math.abs(prev.value - Math.round(prev.value)) < 0.001 && prev.value >= 1 && prev.value <= 6;
                if(prevLooksLikeUnits && ((v >= 100 && v <= 500) || (v >= 10 && v <= 50))) return i;
            }

            for(let i = tokens.length - 1; i >= 0; i--){
                const v = tokens[i].value;
                if(v >= 1 && v <= 5) return i;
            }

            return -1;
        };

        const pickUnitsForGrade = (tokens, gradeIndex) => {
            if(gradeIndex <= 0) return null;
            for(let i = gradeIndex - 1; i >= 0; i--){
                const v = tokens[i].value;
                const isInteger = Math.abs(v - Math.round(v)) < 0.001;
                if(isInteger && v >= 1 && v <= 6) return v;
            }
            return null;
        };

        const extractGradeFromChunk = chunkLines => {
            const standaloneTokens = [];
            const inlineTokens = [];

            for(const line of chunkLines){
                const cleaned = cleanLine(line);
                if(!cleaned) continue;

                if(/^\d{1,3}(?:\.\d{1,2})?$/.test(cleaned)){
                    standaloneTokens.push({ raw: cleaned, value: parseFloat(cleaned), index: 0 });
                    continue;
                }

                if(isSubjectTitleLine(cleaned) || isInstructorLine(cleaned)) continue;

                const rowTokens = parseNumericTokens(cleaned);
                for(const tok of rowTokens) inlineTokens.push(tok);
            }

            const chooseToken = tokenList => {
                if(!tokenList.length) return null;
                const idx = pickGradeTokenIndex(tokenList);
                if(idx < 0) return null;
                return normalizeGradeToken(tokenList, idx);
            };

            return chooseToken(standaloneTokens) || chooseToken(inlineTokens);
        };

        let i = 0;
        while(i < rows.length){
            const current = cleanLine(rows[i]);
            if(!current){
                i++;
                continue;
            }

            if(isSubjectCodeLine(current) || subjectKeywordPattern.test(current)){
                const code = current;
                let j = i + 1;
                const chunk = [];
                while(j < rows.length){
                    const next = cleanLine(rows[j]);
                    if(next && (isSubjectCodeLine(next) || subjectKeywordPattern.test(next))) break;
                    chunk.push(next);
                    j++;
                }

                const titleLine = chunk.find(isSubjectTitleLine) || '';
                const gradeInfo = extractGradeFromChunk(chunk);
                const gradeText = gradeInfo ? gradeInfo.text : '';
                const fallbackSubtitle = current.replace(/\s*[:\-||]+\s*$/, '');

                if(titleLine || fallbackSubtitle){
                    const rawLabel = titleLine ? `${titleLine}` : fallbackSubtitle;
                    const label = resolveSubjectLabel(normalizeSubjectName(rawLabel)) || normalizeSubjectName(rawLabel) || rawLabel;
                    if(gradeInfo && Number.isFinite(gradeInfo.value)){
                        gradeRows.push({ grade: gradeInfo.value, gradeText: gradeText, units: null });
                        subjectEntries.push({ label, grade: gradeInfo.value, gradeText, units: null });
                    }else if(subjectKeywordPattern.test(current) && /\b\d{1,3}(?:\.\d+)?\b/.test(current)){
                        const match = current.match(/\b(\d{1,3}(?:\.\d+)?)\b\s*$/);
                        if(match){
                            const value = normalizeGradeValue(match[1]);
                            if(Number.isFinite(value)){
                                gradeRows.push({ grade: value, gradeText: String(value).replace(/\.0+$/, ''), units: null });
                                const subjectLabel = resolveSubjectLabel(normalizeSubjectName(current.replace(match[0], '').trim())) || normalizeSubjectName(current.replace(match[0], '').trim()) || 'Subject';
                                subjectEntries.push({ label: subjectLabel, grade: value, gradeText: String(value).replace(/\.0+$/, ''), units: null });
                            }
                        }
                    }else{
                        subjectEntries.push({ label, grade: null, gradeText: '', units: null });
                    }
                }

                i = j;
                continue;
            }

            i++;
        }

        const seenSubjectKeys = new Set();
        const pushSubjectEntry = (label, gradeValue, gradeText) => {
            if(!label || !Number.isFinite(gradeValue) || gradeValue < 0 || gradeValue > 100) return;
            const key = `${String(label).trim().toUpperCase()}|${Number(gradeValue).toFixed(2)}`;
            if(seenSubjectKeys.has(key)) return;
            seenSubjectKeys.add(key);
            subjectEntries.push({ label: String(label).trim(), grade: gradeValue, gradeText: String(gradeText || gradeValue), units: null });
            gradeRows.push({ grade: gradeValue, gradeText: String(gradeText || gradeValue), units: null });
        };

        for(const rawLine of rows){
            const line = cleanLine(rawLine);
            if(!line) continue;
            if(metadataLinePattern.test(line)) continue;
            if(isInstructorLine(line)) continue;

            const directSubjectMatch = line.match(/^(\d{4,6})\s+[A-Z0-9]{2,}\s+(.+?)\s+(\d{1,3}(?:\.\d+)?)\s*(?:Passed|Failed|Incomplete|Conditional|Remarks|Approved)?$/i);
            if(directSubjectMatch){
                const subjectCode = directSubjectMatch[1];
                const rawSubjectName = directSubjectMatch[2].replace(new RegExp(`^${subjectCode}\s*`, 'i'), '').trim();
                const gradeText = directSubjectMatch[3];
                const gradeValue = Number.parseFloat(gradeText);
                const cleanedLabel = normalizeSubjectName(rawSubjectName);
                const subjectLabel = resolveSubjectLabel(cleanedLabel) || cleanedLabel;
                if(subjectLabel && Number.isFinite(gradeValue) && gradeValue >= 0 && gradeValue <= 100){
                    pushSubjectEntry(subjectLabel, gradeValue, gradeText);
                    continue;
                }
            }

            const shortSubjectMatch = line.match(/^([A-Z]{2,10})\s+(\d{1,3}(?:\.\d+)?)\s*(?:Passed|Failed|Incomplete|Conditional|Remarks|Approved)?$/i);
            if(shortSubjectMatch){
                const subjectLabel = resolveSubjectLabel(shortSubjectMatch[1].toUpperCase()) || shortSubjectMatch[1].toUpperCase();
                const gradeValue = Number.parseFloat(shortSubjectMatch[2]);
                pushSubjectEntry(subjectLabel, gradeValue, shortSubjectMatch[2]);
                continue;
            }
        }

        if(subjectEntries.length === 0){
            for(const rawLine of rows){
                const line = cleanLine(rawLine);
                if(!line) continue;
                if(metadataLinePattern.test(line)) continue;
                if(isInstructorLine(line)) continue;

                const structuredMatch = line.match(/^(\d{4,6})\s+[A-Z]{2,8}\b.*?(\d{1,3}(?:\.\d+)?)\s*(?:Passed|Failed|Incomplete|Conditional|Remarks|Approved)?$/i);
                if(structuredMatch){
                    const gradeValue = Number.parseFloat(structuredMatch[2]);
                    if(Number.isFinite(gradeValue) && gradeValue >= 0 && gradeValue <= 100){
                        const label = line.replace(new RegExp(`\\b${structuredMatch[2].replace('.', '\\.') }\\b\\s*(?:Passed|Failed|Incomplete|Conditional|Remarks|Approved)?$`, 'i'), '').trim();
                        const cleanedLabel = label.replace(/^(\d{4,6})\s+[A-Z]{2,8}\s*/i, '').trim();
                        if(cleanedLabel){
                            subjectEntries.push({ label: cleanedLabel, grade: gradeValue, gradeText: String(gradeValue), units: null });
                            gradeRows.push({ grade: gradeValue, gradeText: String(gradeValue), units: null });
                        }
                        continue;
                    }
                }

                const numericTokens = parseNumericTokens(line);
                const gradeIndex = pickGradeTokenIndex(numericTokens);
                if(gradeIndex < 0) continue;

                const normalizedGrade = normalizeGradeToken(numericTokens, gradeIndex);
                if(!normalizedGrade) continue;

                const gradeText = normalizedGrade.text;
                const title = line
                    .replace(/\b\d{1,3}(?:\.\d{1,2})?\b/g, ' ')
                    .replace(/[|:]+/g, ' ')
                    .replace(/\s{2,}/g, ' ')
                    .trim();
                const cleanTitle = normalizeSubjectName(title);
                const resolvedTitle = resolveSubjectLabel(cleanTitle) || cleanTitle;

                if((resolvedTitle.match(/[A-Za-z]/g) || []).length < 6) continue;

                subjectEntries.push({ label: resolvedTitle, grade: normalizedGrade.value, gradeText, units: null });
                gradeRows.push({ grade: normalizedGrade.value, gradeText, units: null });
            }
        }

        const numericEntries = subjectEntries.filter(e => Number.isFinite(e.grade));
        const percentLikeCount = numericEntries.filter(e => e.grade >= 50 && e.grade <= 100).length;
        const singleDigitCount = numericEntries.filter(e => e.grade >= 0 && e.grade < 10).length;
        if(numericEntries.length >= 5 && percentLikeCount >= (numericEntries.length - 2) && singleDigitCount > 0){
            for(const e of numericEntries){
                if(e.grade >= 0 && e.grade < 10){
                    const repaired = e.grade + 80;
                    e.grade = repaired;
                    e.gradeText = repaired.toFixed(1);
                }
            }
            for(const g of gradeRows){
                if(g.grade >= 0 && g.grade < 10){
                    const repaired = g.grade + 80;
                    g.grade = repaired;
                    g.gradeText = repaired.toFixed(1);
                }
            }
        }

        const isCodeNoiseLabel = label => {
            const text = String(label || '').trim();
            if(!text) return true;
            if(/^\d{4,6}(\s+[A-Z0-9]{2,10})+\s+/i.test(text)) return true;
            if(/\b(?:ENG1|FIL1|MAT1|ESC|PE1|ET|FCL1|NHG1|BAP|ENG2|STAT|PSC|PE2|TG|NHG2|FIL2|PDV|RES1|FCL2|TPS|CLP|UCS|PE3|EAP|FIL3|FCL3|NHG3|RES2|ENT|FBS)\b/i.test(text)) return true;
            return false;
        };

        const deduped = [];
        const seen = new Set();
        for(const entry of subjectEntries){
            let label = normalizeSubjectName(entry.label || '');
            if(!label) continue;
            const mappedLabel = resolveSubjectLabel(label) || label;
            if(isCodeNoiseLabel(mappedLabel)) continue;
            const gradeNumber = Number.isFinite(entry.grade) ? Number(entry.grade) : Number.parseFloat(String(entry.gradeText || '').replace(/[^0-9.\-]/g, ''));
            const key = `${mappedLabel.toUpperCase()}|${Number.isFinite(gradeNumber) ? gradeNumber.toFixed(2) : 'NA'}`;
            if(seen.has(key)) continue;
            seen.add(key);
            deduped.push({ ...entry, label: mappedLabel, grade: Number.isFinite(gradeNumber) ? gradeNumber : entry.grade });
        }

        const subjectsText = deduped
            .map(e => e.gradeText ? `${e.label} - ${e.gradeText}` : e.label)
            .join('\n');

        const dedupedGrades = [];
        const gradeSeen = new Set();
        for(const entry of deduped.filter(e => Number.isFinite(e.grade))){
            const key = `${String(entry.label || '').trim().toUpperCase()}|${Number(entry.grade).toFixed(2)}`;
            if(gradeSeen.has(key)) continue;
            gradeSeen.add(key);
            dedupedGrades.push({ grade: entry.grade, gradeText: String(entry.gradeText || entry.grade), units: entry.units || null });
        }

        return {
            subjectsText,
            gradeRows: dedupedGrades
        };
    };

    const computeGwaFromGrades = gradeRows => {
        if(!gradeRows || gradeRows.length === 0) return '';

        const cleaned = gradeRows.filter(r => Number.isFinite(r.grade));
        if(cleaned.length === 0) return '';

        const gradesOnly = cleaned.map(r => r.grade);
        const allLikelyGwaScale = gradesOnly.every(v => v >= 1 && v <= 5);
        const inPercentScale = gradesOnly.every(v => v >= 50 && v <= 100);

        const weightedSum = cleaned.reduce((sum, r) => sum + (r.grade * (r.units || 1)), 0);
        const weightedUnits = cleaned.reduce((sum, r) => sum + (r.units || 1), 0);
        const weightedAvg = weightedUnits > 0 ? (weightedSum / weightedUnits) : 0;
        const simpleAvg = gradesOnly.reduce((a, b) => a + b, 0) / gradesOnly.length;

        if(allLikelyGwaScale){
            const hasRealUnits = cleaned.some(r => Number.isFinite(r.units));
            return (hasRealUnits ? weightedAvg : simpleAvg).toFixed(2);
        }

        if(inPercentScale){
            return weightedAvg.toFixed(2);
        }

        return '';
    };

    const studentNumberRaw = explicitMetadata.studentNumber || extractExplicitValue([
        /student\s*(?:number|no|id|#)\s*[:\-]?\s*([A-Za-z0-9\-\s]+)/i,
        /std\.?\s*no\.?\s*[:\-]?\s*([A-Za-z0-9\-\s]+)/i,
        /(\bS?\d{6,9}(?:-\d+)?\b)/,
        /(\b\d{2,}-\d{2,}-\d{3,}\b)/,
        /(\b\d{9}\b)/
    ]);
    const studentNumber = parseStudentNumber(studentNumberRaw);

    const rawName = explicitMetadata.studentName || extractExplicitValue([
        /student\s*name\s*[:\-]?\s*(.+)/i,
        /name\s*of\s*student\s*[:\-]?\s*(.+)/i,
        /name\s*[:\-]?\s*(.+)/i,
        /^([A-Z][A-Za-z]+(?:\s+[A-Z](?:\.|[A-Za-z]+)){1,3})$/
    ]) || findLine(/^[A-Z][A-Za-z\s\-\.]{4,}$/);
    const nameParts = parseName(rawName || 'Unknown');

    const courseText = explicitMetadata.course || extractExplicitValue([
        /course\s*(?:\/\s*major)?\s*[:\-]?\s*(.+)/i,
        /major\s*[:\-]?\s*(.+)/i,
        /program\s*[:\-]?\s*(.+)/i,
        /strand\s*[:\-]?\s*(.+)/i,
        /track\s*[:\-]?\s*(.+)/i,
        /^([A-Z0-9]+(?:[-/][A-Z0-9]+)+)$/,
        /^([A-Z]{2,10}\s*[-/]\s*[A-Z0-9]{2,10})$/
    ]) || findLine(/\b(course|program|strand|track)\b/i).replace(/(?:course\s*[:\-]?|program\s*[:\-]?|strand\s*[:\-]?|track\s*[:\-]?)/i, '').trim();
    const normalizedCourseText = (courseText || '')
        .replace(/^\/+\s*[A-Za-z]+\s*[:\-]?\s*/i, '')
        .replace(/\.+$/, '')
        .replace(/\s{2,}/g, ' ')
        .trim();

    const age = findValue([
        /age\s*[:\-]?\s*([0-9]{1,3})\b/i,
        /(\b[1-9][0-9]?\s*(?:yrs?|years?)\b)/i
    ]);

    const gwa = findValue([
        /(?:gwa|gpa|average|overall\s*grade)\s*[:\-]?\s*([0-9]{1,3}(?:\.[0-9]+)?)/i
    ]);

    const grades = findValue([
        /grades?\s*[:\-]?\s*([A-Fa-f0-9\+\-\/,\. ]+)/i,
        /final\s*grade\s*[:\-]?\s*([A-Fa-f0-9\+\-\/,\. ]+)/i
    ]);

    const subjectParse = parseSubjectRows();
    const subjects = subjectParse.subjectsText;
    const gradeRows = subjectParse.gradeRows;
    const computedGwa = computeGwaFromGrades(gradeRows);
    const mergedGrades = grades || gradeRows.map(r => r.gradeText).join(', ');
    const finalStudentNumber = studentNumber || ('S' + (Date.now() % 1000000).toString().padStart(6, '0'));

    if(tableLayout){
        const tableNameParts = parseName(tableLayout.studentName);
        const tableSubjects = tableLayout.subjects;
        const tableGrades = tableSubjects.map(item => String(item.grade));
        const tableGwa = tableSubjects.length
            ? (tableSubjects.reduce((sum, s) => sum + s.grade, 0) / tableSubjects.length).toFixed(2)
            : '';

        return {
            firstName: tableNameParts.firstName || 'Unknown',
            middleInitial: tableNameParts.middleInitial || '',
            lastName: tableNameParts.lastName || '',
            studentNumber: tableLayout.studentNumber || finalStudentNumber,
            course: tableLayout.course || normalizedCourseText || '',
            academicRecord: age || '',
            gwa: gwa || computedGwa || tableGwa,
            grades: [...new Set(tableGrades)].join(', '),
            subjects: tableSubjects.map(item => `${item.label} - ${item.grade}`).join('\n')
        };
    }

    if(compactLayout){
        const compactSubjects = compactLayout.subjects;
        const compactGrades = compactSubjects.map(item => String(item.grade));
        const compactNameParts = parseName(compactLayout.studentName);
        const compactGwa = compactSubjects.length
            ? (compactSubjects.reduce((sum, s) => sum + s.grade, 0) / compactSubjects.length).toFixed(2)
            : '';

        return {
            firstName: compactNameParts.firstName || 'Unknown',
            middleInitial: compactNameParts.middleInitial || '',
            lastName: compactNameParts.lastName || '',
            studentNumber: compactLayout.studentNumber || finalStudentNumber,
            course: compactLayout.course || normalizedCourseText || '',
            academicRecord: age || '',
            gwa: gwa || computedGwa || compactGwa,
            grades: [...new Set(compactGrades)].join(', '),
            subjects: compactSubjects.map(item => `${item.label} - ${item.grade}`).join('\n')
        };
    }

    return {
        firstName: nameParts.firstName || 'Unknown',
        middleInitial: nameParts.middleInitial || '',
        lastName: nameParts.lastName || '',
        studentNumber: finalStudentNumber,
        course: normalizedCourseText || '',
        academicRecord: age || '',
        gwa: gwa || computedGwa || '',
        grades: mergedGrades || '',
        subjects: subjects || ''
    };
}
function normalizeStudentNumber(value){
    return String(value || '').replace(/[^A-Z0-9-]/gi, '').toUpperCase();
}

function studentNumberKey(value){
    return normalizeStudentNumber(value).replace(/-/g, '');
}

function splitOcrStudentName(value){
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    if(!text) return { firstName: '', middleInitial: '', lastName: '' };

    let firstName = '';
    let middleInitial = '';
    let lastName = '';
    if(text.includes(',')){
        const parts = text.split(',').map(part => part.trim()).filter(Boolean);
        lastName = parts.shift() || '';
        const givenNames = (parts.join(' ') || '').split(/\s+/).filter(Boolean);
        const possibleInitial = givenNames[givenNames.length - 1] || '';
        if(/^[A-Za-z]{1,2}\.$/.test(possibleInitial)){
            middleInitial = possibleInitial;
            givenNames.pop();
        }
        firstName = givenNames.join(' ');
    }else{
        const parts = text.split(/\s+/).filter(Boolean);
        firstName = parts.shift() || '';
        lastName = parts.pop() || '';
        const possibleInitial = parts[parts.length - 1] || '';
        if(/^[A-Za-z]{1,2}\.$/.test(possibleInitial)){
            middleInitial = possibleInitial;
            parts.pop();
        }
        firstName = [firstName, ...parts].filter(Boolean).join(' ');
    }

    middleInitial = middleInitial.replace(/[^A-Za-z]/g, '').slice(0, 2).toUpperCase();
    return { firstName, middleInitial, lastName };
}

function normalizeOcrStrand(value){
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    const upper = text.toUpperCase();
    const known = configuredAcademicStrands;
    const match = known.find(item => upper.includes(item.replace('_', ' ')) || upper.includes(item));
    return match || text;
}

function studentMetadataFromOcr(parsed = {}, fallbackText = ''){
    const fallback = parseStudent(fallbackText || '') || {};
    const nameParts = splitOcrStudentName(parsed.student_name || fallback.studentName || '');
    return {
        studentNumber: normalizeStudentNumber(parsed.student_no || fallback.studentNumber || ''),
        firstName: nameParts.firstName || fallback.firstName || '',
        middleInitial: nameParts.middleInitial || fallback.middleInitial || '',
        lastName: nameParts.lastName || fallback.lastName || '',
        strand: normalizeOcrStrand(parsed.strand || parsed.major || fallback.course || '')
    };
}

function updateOcrIdentityPreview(metadata){
    const preview = document.getElementById('ocr-identity-preview');
    if(!preview) return;
    const escapeText = value => String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    const values = [
        ['Student number', metadata?.studentNumber],
        ['Name', [metadata?.firstName, metadata?.middleInitial ? `${metadata.middleInitial}.` : '', metadata?.lastName].filter(Boolean).join(' ')],
        ['Strand', metadata?.strand]
    ];
    preview.innerHTML = values.map(([label, value]) => `<span><strong>${label}</strong><em>${escapeText(value || 'Not detected')}</em></span>`).join('');
}

function majorSubjectCategory(label){
    const text = String(label || '').toLowerCase();
    if(/math|algebra|calculus|statistics|trigonometry|geometry|probability/.test(text)) return 'Math';
    if(/science|physics|chemistry|biology|environment|anatomy|physiology/.test(text)) return 'Science';
    if(/english|literature|reading|writing|communication|speech/.test(text)) return 'English';
    return '';
}

function strongestMajorSubjectRows(rows){
    const strongest = new Map();
    for(const row of rows || []){
        const category = majorSubjectCategory(row.label || row.subject || '');
        const grade = Number.parseFloat(row.grade);
        if(!category || !Number.isFinite(grade)) continue;
        if(!strongest.has(category) || grade > strongest.get(category).grade){
            strongest.set(category, {label: category, grade});
        }
    }
    return ['Math', 'Science', 'English'].filter(category => strongest.has(category)).map(category => strongest.get(category));
}

function parseAcademicRecord(text){
    const tableLines = String(text || '').split(/\r?\n/)
        .map(line => line.trim())
        .filter(line => line.includes('|'));
    if(tableLines.length > 1){
        const grades = tableLines.slice(1).map(line => {
            const matches = [...line.matchAll(/\b(\d{1,3}(?:\.\d{1,2})?)\b/g)]
                .map(match => Number.parseFloat(match[1]))
                .filter(value => value >= 70 && value <= 100);
            return matches[matches.length - 1];
        }).filter(value => Number.isFinite(value));
        return {
            firstName: 'Unknown',
            middleInitial: '',
            lastName: '',
            studentNumber: 'S' + (Date.now() % 1000000).toString().padStart(6, '0'),
            course: '',
            academicRecord: '',
            gwa: grades.length ? (grades.reduce((sum, value) => sum + value, 0) / grades.length).toFixed(2) : '',
            grades: grades.join(', '),
            subjects: tableLines.join('\n')
        };
    }
    const directRows = [];
    for(const rawLine of String(text || '').split(/\r?\n/)){
        const line = rawLine.replace(/\s+/g, ' ').trim();
        const match = line.match(/^(.+?)\s+-\s+(\d{1,3}(?:\.\d{1,2})?)$/);
        if(!match) continue;
        const grade = Number.parseFloat(match[2]);
        const label = match[1].trim();
        if(label && Number.isFinite(grade) && grade >= 0 && grade <= 100){
            directRows.push({ label, grade });
        }
    }
    if(directRows.length > 0){
        const directGrades = directRows.map(row => row.grade);
        return {
            firstName: 'Unknown',
            middleInitial: '',
            lastName: '',
            studentNumber: 'S' + (Date.now() % 1000000).toString().padStart(6, '0'),
            course: '',
            academicRecord: '',
            gwa: (directGrades.reduce((sum, value) => sum + value, 0) / directGrades.length).toFixed(2),
            grades: directGrades.join(', '),
            subjects: directRows.map(row => `${row.label} - ${row.grade}`).join('\n')
        };
    }
    const parsed = parseStudent(text || '') || {};
    const cleanSubjectRows = [];
    const seenSubjects = new Set();
    const ignoredLabels = /^(?:subject|subject name|subject code|course|description|units?|instructor|remarks?|grade|final grade|gwa|gpa|general weighted average|semester|term|school year)$/i;
    const gradeAtEnd = /(?:^|\s|-|:|\|)(\d{1,3}(?:\.\d{1,2})?)\s*(?:passed|failed|incomplete|conditional|approved|remarks?)?$/i;

    const addSubjectRow = (label, grade) => {
        label = String(label || '')
            .replace(/\s+/g, ' ')
            .replace(/^[-:|]+|[-:|]+$/g, '')
            .trim();
        grade = Number.parseFloat(grade);
        if(!label || !Number.isFinite(grade) || grade < 0 || grade > 100) return;
        if(ignoredLabels.test(label) || /\b(?:instructor|professor|teacher|remarks?|units?|credit)\b/i.test(label)) return;
        if((label.match(/[A-Za-z]/g) || []).length < 4) return;

        const key = `${label.toUpperCase()}|${grade.toFixed(2)}`;
        if(seenSubjects.has(key)) return;
        seenSubjects.add(key);
        cleanSubjectRows.push({ label, grade });
    };

    for(const rawLine of String(text || '').split(/\r?\n/)){
        const line = rawLine.replace(/\s+/g, ' ').trim();
        const codeMatch = line.match(/^\s*\d{4,6}\s+[A-Z0-9]{2,8}\s+/i);
        if(!codeMatch) continue;

        const codeMatches = [...line.matchAll(/\b(\d{4,6})\s+([A-Z0-9]{2,8})\b/gi)];
        if(codeMatches.length > 1){
            for(let index = 0; index < codeMatches.length; index++){
                const codeEntry = codeMatches[index];
                const nextEntry = codeMatches[index + 1];
                const segmentEnd = nextEntry ? nextEntry.index : line.length;
                const segment = line.slice(codeEntry.index, segmentEnd);
                const segmentGrades = [...segment.matchAll(/\b(\d{1,3}(?:\.\d{1,2})?)\b/g)]
                    .map(match => Number.parseFloat(match[1]))
                    .filter(value => Number.isFinite(value) && value >= 0 && value <= 100);
                const codeKey = `${codeEntry[1]} ${codeEntry[2]}`.toUpperCase();
                const subjectLabel = subjectCatalog[codeKey] || subjectCatalog[codeEntry[1]] || subjectCatalog[codeEntry[2].toUpperCase()];
                if(subjectLabel && segmentGrades.length){
                    addSubjectRow(subjectLabel, segmentGrades[segmentGrades.length - 1]);
                }
            }
            continue;
        }

        const numericMatches = [...line.matchAll(/\b\d{1,3}(?:\.\d{1,2})?\b/g)];
        if(numericMatches.length < 3) continue;
        const finalTerms = numericMatches.slice(-3);
        const subjectPart = line
            .slice(codeMatch[0].length, finalTerms[0].index)
            .trim();
        addSubjectRow(subjectPart, finalTerms[2][0]);
    }

    for(const rawLine of String(text || '').split(/\r?\n/)){
        const line = rawLine.replace(/\s+/g, ' ').trim();
        if([...line.matchAll(/\b\d{4,6}\s+[A-Z0-9]{2,8}\b/gi)].length > 1) continue;
        const gradeMatch = line.match(/\b(\d{1,3}(?:\.\d{1,2})?)\s*(?:Passed|Failed|Incomplete|Conditional|Approved|Remarks)?\s*$/i);
        if(!gradeMatch) continue;
        const grade = Number.parseFloat(gradeMatch[1]);
        if(!Number.isFinite(grade) || grade < 0 || grade > 100) continue;

        const beforeGrade = line.slice(0, gradeMatch.index).trim();
        const instructorMatch = beforeGrade.match(/\b[A-Z][A-Z.'-]{1,},/);
        if(!instructorMatch) continue;

        let subjectPart = beforeGrade.slice(0, instructorMatch.index).trim();
        subjectPart = subjectPart.replace(/^\d{1,6}\s+/, '');
        subjectPart = subjectPart.replace(/\s+\d{1,2}(?:\.\d{1,2})?\s*$/, '').trim();

        const tokens = subjectPart.split(/\s+/);
        while(tokens.length && /^[A-Z0-9]{2,10}$/.test(tokens[0])) tokens.shift();
        addSubjectRow(tokens.join(' '), grade);
    }

    if(cleanSubjectRows.length === 0){
        for(const rawLine of String(parsed.subjects || '').split(/\r?\n/)){
        let line = rawLine.replace(/\s+/g, ' ').trim();
        if(!line) continue;

        const gradeMatch = line.match(gradeAtEnd);
        if(!gradeMatch) continue;

        const grade = Number.parseFloat(gradeMatch[1]);
        if(!Number.isFinite(grade) || grade < 0 || grade > 100) continue;

        let label = line.slice(0, gradeMatch.index + gradeMatch[0].length - gradeMatch[0].length).trim();
        label = label
            .replace(/\s*(?:-|:|\|)\s*$/, '')
            .replace(/^\d{4,6}\s+[A-Z0-9]{2,10}\s+/i, '')
            .replace(/^(?:[A-Z]{2,10}\s+\d{1,3}(?:\.\d{1,2})?)\s+/i, '')
            .replace(/\s*\([^)]*\)\s*$/, '')
            .trim();

        label = label.split(/\s*,\s*/)[0].trim();
        label = label.replace(/\s+(?:instructor|teacher|professor)\s*[:\-].*$/i, '').trim();
        label = label.replace(/\s{2,}/g, ' ').replace(/^[-:|]+|[-:|]+$/g, '').trim();

            addSubjectRow(label, grade);
        }
    }

    if(cleanSubjectRows.length === 0){
        for(const rawLine of String(text || '').split(/\r?\n/)){
            let line = rawLine.replace(/\s+/g, ' ').trim();
            if(!line || /\b(?:student|name|number|address|birth|gender|course|strand|track|semester|school year|subject code|subject name|instructor|remarks?|gwa|gpa|general weighted average)\b/i.test(line)) continue;

            const gradeMatch = line.match(gradeAtEnd) || line.match(/(?:^|\s|-|:|\|)(\d{1,3}(?:\.\d{1,2})?)(?=\s*,)/i);
            if(!gradeMatch) continue;
            const grade = Number.parseFloat(gradeMatch[1]);
            if(!Number.isFinite(grade) || grade < 0 || grade > 100) continue;

            let label = line.slice(0, gradeMatch.index)
                .replace(/^\d{4,6}\s+[A-Z0-9]{2,10}\s+/i, '')
                .replace(/\s*(?:-|:|\|)\s*$/, '')
                .split(/\s*,\s*/)[0]
                .replace(/\s{2,}/g, ' ')
                .trim();
            if(label.length < 3 || ignoredLabels.test(label)) continue;

            const key = `${label.toUpperCase()}|${grade.toFixed(2)}`;
            if(seenSubjects.has(key)) continue;
            seenSubjects.add(key);
            cleanSubjectRows.push({ label, grade });
        }
    }

    const subjectText = cleanSubjectRows.map(row => `${row.label} - ${row.grade}`).join('\n');
    const gradeValues = cleanSubjectRows.map(row => row.grade);
    const calculatedGwa = gradeValues.length
        ? (gradeValues.reduce((sum, value) => sum + value, 0) / gradeValues.length).toFixed(2)
        : '';
    const explicitGwaMatches = [...String(text || '').matchAll(/\b(?:gwa|gpa|general\s+(?:weighted\s+)?average)\s*[:\-]?\s*(\d{1,3}(?:\.\d{1,3})?)/gi)];
    const explicitGwaMatch = explicitGwaMatches[explicitGwaMatches.length - 1];
    const parsedGwa = explicitGwaMatch
        ? Number.parseFloat(explicitGwaMatch[1])
        : NaN;

    if(!subjectText){
        return null;
    }

    return {
        subjects: subjectText,
        grades: gradeValues.join(', '),
        gwa: Number.isFinite(parsedGwa) ? parsedGwa.toFixed(2) : calculatedGwa
    };
}

function normalizeStudentName(value){
    return String(value || '')
        .replace(/[^A-Z\s]/gi, '')
        .replace(/\s+/g, ' ')
        .trim()
        .toUpperCase();
}

function mergeSubjectText(existingText, incomingText){
    const lines = [];
    const seen = new Set();
    for(const text of [existingText, incomingText]){
        if(!text) continue;
        for(const raw of String(text).split(/\n|\r\n/)){
            const line = String(raw).trim();
            if(!line) continue;
            const clean = line.replace(/\s+/g, ' ');
            const key = clean.toLowerCase();
            if(!seen.has(key)){
                seen.add(key);
                lines.push(clean);
            }
        }
    }
    return lines.join('\n');
}

function autoAddStudentFromText(text){
    const student = parseStudent(text);
    if(!student) return null;

    const newName = normalizeStudentName([student.firstName, student.middleInitial, student.lastName].join(' '));

    const normalizeStudentEntry = entry => {
        if(!entry) return null;
        const existingName = normalizeStudentName([entry.firstName, entry.middleInitial, entry.lastName].join(' '));
        return {
            number: studentNumberKey(entry.studentNumber),
            name: existingName,
            sameNumber: !!(entry.studentNumber && student.studentNumber && studentNumberKey(entry.studentNumber) === studentNumberKey(student.studentNumber)),
            sameName: !!existingName && !!newName && existingName === newName,
            course: String(entry.course || '').trim().toUpperCase(),
            incomingCourse: String(student.course || '').trim().toUpperCase(),
        };
    };

    const existingIndex = students.findIndex(item => {
        const entry = normalizeStudentEntry(item);
        if(!entry) return false;
        return entry.sameNumber || entry.sameName || (
            entry.course && entry.incomingCourse && entry.course === entry.incomingCourse &&
            entry.name && newName && entry.name === newName
        );
    });

    if(existingIndex >= 0){
        const existing = students[existingIndex];
        const mergedSubjects = mergeSubjectText(existing.subjects, student.subjects);
        const mergedGrades = [...new Set((existing.grades || '').split(',').concat((student.grades || '').split(',')).filter(Boolean).map(v => v.trim()))].join(', ');
        students[existingIndex] = {
            ...existing,
            ...student,
            subjects: mergedSubjects,
            grades: mergedGrades || existing.grades || student.grades,
            gwa: student.gwa || existing.gwa,
            course: student.course || existing.course,
            studentNumber: student.studentNumber || existing.studentNumber,
            firstName: student.firstName || existing.firstName,
            middleInitial: student.middleInitial || existing.middleInitial,
            lastName: student.lastName || existing.lastName,
        };
        renderStudents();
        viewStudentProfile(students[existingIndex]);
        return students[existingIndex];
    }

    students.push(student);
    renderStudents();
    viewStudentProfile(student);
    return student;
}
 
 

function renderStudents() {
 
    let studentList = document.getElementById("studentList");
 
    studentList.innerHTML = "";
 
    students.forEach((student, index) => {
 
        let studentCard = document.createElement("div");
 
        studentCard.className = "coursecard";
 
        studentCard.innerHTML =
            "<strong>" + student.studentNumber + "</strong><br>" +
            student.firstName + " " +
            student.middleInitial + " " +
            student.lastName;
 
        studentCard.onclick = function () {
 
            document.querySelectorAll(".coursecard")
                .forEach(card => card.classList.remove("selected"));
 
            studentCard.classList.add("selected");
 
            selectedStudent = index;
 
            viewStudentProfile(student);
        };
 
        studentList.appendChild(studentCard);
    });
}
 
 

function viewStudentProfile(student) {
 
    let profile = document.getElementById("studentProfile");
 
    profile.innerHTML = `
        <h3>Student Profile</h3>
 
        <p><strong>Student Number:</strong>
        ${student.studentNumber}</p>
        
        <br>
 
        <p><strong>First Name:</strong>
        ${student.firstName}</p>
 
        <p><strong>Middle Initial:</strong>
        ${student.middleInitial}</p>
 
        <p><strong>Last Name:</strong>
        ${student.lastName}</p>

        <p><strong>Age:</strong>
        ${student.academicRecord}</p>
 
        <br>

        <p><strong>Course:</strong>
        ${student.course}</p>

        <br>

        <p><strong>GWA:</strong>
        ${student.gwa || 'N/A'}</p>

        <p><strong>Grades:</strong>
        ${student.grades || 'N/A'}</p>

        <br>

        <p><strong>Subjects:</strong></p>

        <p style="white-space: pre-line;">
        ${student.subjects || 'N/A'}
        </p>

    `;
}
 
 

function editStudent() {
 
    if (selectedStudent === null) {
        alert("Please select a student first.");
        return;
    }
 
    let student = students[selectedStudent];
 
    let newFirstName = prompt("Enter First Name:", student.firstName);
    if (newFirstName === null) return;
 
    let newMI = prompt("Enter Middle Initial:", student.middleInitial);
    if (newMI === null) return;
 
    let newLastName = prompt("Enter Last Name:", student.lastName);
    if (newLastName === null) return;
 
    let newStudentNumber = prompt("Enter Student Number:", student.studentNumber);
    if (newStudentNumber === null) return;
 
    let newCourse = prompt("Enter Course:", student.course);
    if (newCourse === null) return;
 
    let newAcademicRecord = prompt("Enter Academic Record:", student.academicRecord);
    if (newAcademicRecord === null) return;

    let newGWA = prompt("Enter GWA:", student.gwa || '');
    if (newGWA === null) return;

    let newGrades = prompt("Enter Grades:", student.grades || '');
    if (newGrades === null) return;

    let newSubjects = prompt("Enter Subjects:", student.subjects || '');
    if (newSubjects === null) return;
 
    student.firstName = newFirstName;
    student.middleInitial = newMI;
    student.lastName = newLastName;
    student.studentNumber = newStudentNumber;
    student.course = newCourse;
    student.academicRecord = newAcademicRecord;
    student.gwa = newGWA;
    student.grades = newGrades;
    student.subjects = newSubjects;
 
    renderStudents();
 
    viewStudentProfile(student);
 
    alert("Student Updated Successfully!");
}
 
 

function deleteStudent() {
 
    if (selectedStudent === null) {
        alert("Please select a student first.");
        return;
    }
 
    let confirmDelete =
        confirm("Are you sure you want to delete this student?");
 
    if (!confirmDelete) {
        return;
    }
 
    students.splice(selectedStudent, 1);
 
    selectedStudent = null;
 
    document.getElementById("studentProfile").innerHTML =
        "<h3>Student Profile</h3><p>Select a student to view details.</p>";
 
    renderStudents();
 
    alert("Student Deleted Successfully!");
}
 
 

function clearInputs() {
    const ids = ["firstName","middleInitial","lastName","studentNumber","course","academicRecord","age"];
    ids.forEach(id => { const el = document.getElementById(id); if(el) el.value = ''; });
}

(function(){
    function loadScript(src){
        return new Promise((resolve, reject) => {
            if(document.querySelector('script[src="' + src + '"]')) return resolve();
            const s = document.createElement('script');
            s.src = src;
            s.onload = () => resolve();
            s.onerror = () => reject(new Error('Failed to load ' + src));
            document.head.appendChild(s);
        });
    }

    let progressWrapEl = null;
    let progressFillEl = null;
    let progressLabelEl = null;

    function showProgress(){
        if(progressWrapEl) progressWrapEl.style.display = 'block';
    }

    function hideProgress(){
        if(progressWrapEl) progressWrapEl.style.display = 'none';
    }

    function setProgress(percent, label){
        showProgress();
        if(progressFillEl){
            if(typeof percent === 'number' && !isNaN(percent)){
                progressFillEl.classList.remove('indeterminate');
                progressFillEl.style.width = Math.max(0, Math.min(100, percent)) + '%';
            }else{
                progressFillEl.classList.add('indeterminate');
            }
        }
        if(progressLabelEl && label !== undefined){
            progressLabelEl.textContent = label;
        }
    }

    async function imageBlobToText(blob){
        try{
            setProgress(null, 'Running Docling OCR and TableFormer...');
            const formData = new FormData();
            formData.append('report_card', blob, blob.name || 'report-card.png');

            const response = await fetch('/api/v1/ocr_report_card', { method: 'POST', body: formData });
            let data;
            try{
                data = await response.json();
            }catch(parseError){
                throw new Error(`Server did not return a valid response (status ${response.status}). It may have run out of memory or timed out processing this file. Please try again in a moment.`);
            }

            if(!data || !data.success){
                throw new Error((data && data.message) || 'Docling could not process this file.');
            }

            setProgress(90, `${data.provider || 'OCR'} extraction complete.`);
            window.latestOcrReviewRequired = Boolean(window.latestOcrReviewRequired || data.review_required);
            window.latestParsedSubjects = Array.isArray(data.parsed?.subjects) ? data.parsed.subjects : [];
            const reviewErrorSubjects = (Array.isArray(data.parsed?.validation_errors) ? data.parsed.validation_errors : [])
                .map(error => {
                    const message = String(error || '');
                    const match = message.match(/^Needs review:\s*(.+)$/i)
                        || message.match(/^Grade rejected for\s+(.+?)(?::\s*.*)?$/i);
                    return match ? {subject_name: match[1].trim(), needs_review: true, reason: message} : null;
                })
                .filter(Boolean);
            window.latestOcrReviewSubjects = [
                ...(Array.isArray(window.latestOcrReviewSubjects) ? window.latestOcrReviewSubjects : []),
                ...window.latestParsedSubjects.filter(subject => subject?.needs_review),
                ...reviewErrorSubjects,
            ];
            if(!window.latestSavedProfile?.success){
                window.latestStudentMetadata = studentMetadataFromOcr(data.parsed || {}, data.structured_text || '');
                updateOcrIdentityPreview(window.latestStudentMetadata);
            }
            window.latestDoclingTable = data.raw_ocr && Array.isArray(data.raw_ocr.table)
                ? data.raw_ocr.table
                : [];
            const structuredText = String(data.structured_text || '').trim();
            window.latestDoclingRawText = structuredText;
            if(data.automatic_ocr_disabled){
                window.latestAutomaticOcrDisabled = true;
            }else if(!structuredText){
                const diagnostics = data.diagnostics || {};
                throw new Error(
                    `Docling returned no subject rows (table rows: ${diagnostics.table_rows || 0}, `
                    + `docling subjects: ${diagnostics.docling_subjects || 0}, `
                    + `parsed subjects: ${diagnostics.parsed_subjects || 0}, `
                    + `raw text characters: ${diagnostics.raw_text_characters || 0}, `
                    + `preview: ${diagnostics.table_preview || 'none'}).`
                );
            }
            return structuredText;
        }catch(e){
            console.error('imageBlobToText (Docling) error:', e);
            throw e;
        }
    }

    async function extractTextFromPDF(file){
        try{
            await loadScript('https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.min.js');
            const pdfjsLib = window['pdfjsLib'];
            if(pdfjsLib && pdfjsLib.GlobalWorkerOptions){
                pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.worker.min.js';
            }
            const arrayBuffer = await file.arrayBuffer();
            const loadingTask = pdfjsLib.getDocument({data: arrayBuffer});
            const pdf = await loadingTask.promise;
            let fullText = '';
            for(let i=1;i<=pdf.numPages;i++){
                const page = await pdf.getPage(i);
                const textContent = await page.getTextContent();
                const rowsByY = new Map();
                for(const item of textContent.items){
                    const y = item.transform && item.transform.length > 5 ? Math.round(item.transform[5]) : 0;
                    if(!rowsByY.has(y)) rowsByY.set(y, []);
                    rowsByY.get(y).push(item);
                }

                const pageText = [...rowsByY.entries()]
                    .sort((a, b) => b[0] - a[0])
                    .map(([, rowItems]) => rowItems.map(it => it.str).join(' '))
                    .join('\n');
                console.log(`PDF page ${i} extracted text length:`, pageText.length);
                if(pageText && pageText.trim().length>20){
                    fullText += pageText + '\n\n';
                    continue;
                }
                console.log(`PDF page ${i} is scanned, running OCR...`);
                const viewport = page.getViewport({scale: 2.0});
                const canvas = document.createElement('canvas');
                canvas.width = viewport.width;
                canvas.height = viewport.height;
                const ctx = canvas.getContext('2d');
                await page.render({canvasContext: ctx, viewport}).promise;
                const blob = await new Promise(r=>canvas.toBlob(r,'image/png'));
                setProgress(Math.round((i/pdf.numPages)*100), `OCR page ${i}/${pdf.numPages}`);
                const pageOcr = await imageBlobToText(blob);
                console.log(`PDF page ${i} OCR text length:`, pageOcr ? pageOcr.length : 0);
                fullText += pageOcr + '\n\n';
            }
            console.log('Total PDF text extracted:', fullText.length);
            return fullText;
        }catch(e){
            console.error('PDF extraction failed', e);
            throw e;
        }
    }

    async function extractTextFromDocx(file){
        try{
            await loadScript('https://cdnjs.cloudflare.com/ajax/libs/mammoth/1.4.19/mammoth.browser.min.js');
            console.log('Extracting DOCX...');
            const arrayBuffer = await file.arrayBuffer();
            const { value } = await window.mammoth.extractRawText({arrayBuffer});
            console.log('DOCX extracted text length:', value ? value.length : 0);
            if(value && value.trim().length>0) return value;
            console.warn('DOCX extraction returned empty text');
            return '';
        }catch(e){
            console.error('DOCX extraction failed', e);
            return '';
        }
    }

    document.addEventListener('DOMContentLoaded', async ()=>{
        const input = document.getElementById('image-input');
        if(!input) return;

        try{
            const settingsResponse = await fetch('/api/v1/system-settings/public');
            const settingsData = await settingsResponse.json();
            if(settingsData.success && Array.isArray(settingsData.settings?.available_strands) && settingsData.settings.available_strands.length){
                configuredAcademicStrands = settingsData.settings.available_strands;
            }
            const recommendationLimit = Number(settingsData.settings?.recommendation_limit);
            if(settingsData.success && Number.isInteger(recommendationLimit) && recommendationLimit >= 1 && recommendationLimit <= 5){
                configuredRecommendationLimit = recommendationLimit;
            }
        }catch(error){
            console.warn('Unable to load configured academic strands.', error);
        }

        const isGuest = document.body.dataset.guest === 'true';

        const preview = document.getElementById('preview');
        const previewContainer = preview ? preview.parentElement : null;
        const runBtn = document.getElementById('run-ocr');
        const recommendBtn = document.getElementById('open-recommendations');
        const dropzone = document.getElementById('dropzone');
        const consentCheckbox = document.getElementById('privacy-consent');
        const fileActions = document.getElementById('file-actions');
        const replaceFilesBtn = document.getElementById('replace-files');
        const clearFilesBtn = document.getElementById('clear-files');
        const uploadStatus = document.getElementById('upload-status');
        const uploadError = document.getElementById('upload-error');

        const termsOverlay = document.getElementById('terms-modal-overlay');
        const termsBody = document.getElementById('terms-modal-body');
        const termsAgreeBtn = document.getElementById('terms-agree-btn');
        const termsDeclineBtn = document.getElementById('terms-decline-btn');
        const termsCloseBtn = document.getElementById('terms-modal-close');
        const termsScrollHint = document.getElementById('terms-scroll-hint');
        const openTermsLink = document.getElementById('open-terms-link');
        const profilePrivacyLink = document.getElementById('profile-privacy-link');
        const profileMenuTrigger = document.getElementById('profile-menu-trigger');
        const profileMenu = document.getElementById('profile-menu');
        let selectedFileIndex = null;
        let queuedFiles = [];

        function checkTermsScrollProgress(){
            if(!termsBody || !termsAgreeBtn) return;
            const reachedBottom = termsBody.scrollTop + termsBody.clientHeight >= termsBody.scrollHeight - 12;
            if(reachedBottom){
                termsAgreeBtn.disabled = false;
                if(termsScrollHint) termsScrollHint.classList.add('hidden');
            }
        }

        function openTermsModal(){
            if(!termsOverlay) return;
            termsOverlay.style.display = 'flex';
            if(termsAgreeBtn) termsAgreeBtn.disabled = true;
            if(termsScrollHint) termsScrollHint.classList.remove('hidden');
            if(termsBody) termsBody.scrollTop = 0;
            requestAnimationFrame(checkTermsScrollProgress);
        }

        function closeTermsModal(){
            if(termsOverlay) termsOverlay.style.display = 'none';
        }

        function closeProfileMenu(){
            if(!profileMenu || !profileMenuTrigger) return;
            profileMenu.hidden = true;
            profileMenuTrigger.setAttribute('aria-expanded', 'false');
        }

        if(profileMenuTrigger && profileMenu){
            profileMenuTrigger.addEventListener('click', event => {
                event.stopPropagation();
                const willOpen = profileMenu.hidden;
                profileMenu.hidden = !willOpen;
                profileMenuTrigger.setAttribute('aria-expanded', String(willOpen));
                if(willOpen) profileMenu.querySelector('a,button')?.focus();
            });
            profileMenu.addEventListener('click', event => {
                const gradesButton = event.target.closest('[data-show-grades]');
                if(gradesButton){
                    event.preventDefault();
                    showSavedGrades();
                    closeProfileMenu();
                }
                const uploadButton = event.target.closest('[data-show-upload]');
                if(uploadButton){
                    event.preventDefault();
                    window.showStudentUpload?.();
                    closeProfileMenu();
                }
                event.stopPropagation();
            });
            document.addEventListener('click', closeProfileMenu);
            document.addEventListener('keydown', event => {
                if(event.key === 'Escape' && !profileMenu.hidden){
                    closeProfileMenu();
                    profileMenuTrigger.focus();
                }
            });
        }

        if(termsBody){
            termsBody.addEventListener('scroll', checkTermsScrollProgress);
        }

        if(openTermsLink){
            openTermsLink.addEventListener('click', (e)=>{ e.preventDefault(); openTermsModal(); });
        }

        if(profilePrivacyLink){
            profilePrivacyLink.addEventListener('click', ()=>{
                closeProfileMenu();
                openTermsModal();
            });
        }

        if(termsAgreeBtn){
            termsAgreeBtn.addEventListener('click', ()=>{
                if(termsAgreeBtn.disabled) return;
                if(consentCheckbox){
                    consentCheckbox.disabled = false;
                    consentCheckbox.checked = true;
                }
                closeTermsModal();
            });
        }

        if(termsDeclineBtn){
            termsDeclineBtn.addEventListener('click', closeTermsModal);
        }

        if(termsCloseBtn){
            termsCloseBtn.addEventListener('click', closeTermsModal);
        }

        if(termsOverlay){
            termsOverlay.addEventListener('click', (e)=>{
                if(e.target === termsOverlay) closeTermsModal();
            });
        }

        function parseFilesFromInput(fileList) {
            const files = Array.from(fileList || []);
            if(!files.length) return [];
            return files.filter(file => file && file.name).slice(0, 10);
        }

        function isSupportedReportCardFile(file){
            const name = String(file?.name || '').toLowerCase();
            const extensionSupported = /\.(jpg|jpeg|png|webp|pdf|docx)$/.test(name);
            const type = String(file?.type || '').toLowerCase();
            const typeSupported = !type || type.startsWith('image/') || type === 'application/pdf' || type === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
            return extensionSupported && typeSupported;
        }

        function setUploadMessage(message, type = 'status'){
            if(uploadStatus){
                uploadStatus.textContent = type === 'status' ? message : '';
                uploadStatus.hidden = type !== 'status' || !message;
            }
            if(uploadError){
                uploadError.innerHTML = type === 'error' && message
                    ? `<i class="fa-solid fa-circle-exclamation"></i><span>${String(message).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</span>`
                    : '';
                uploadError.hidden = type !== 'error' || !message;
            }
        }

        function setInputFiles(files){
            try{
                const transfer = new DataTransfer();
                files.forEach(file => transfer.items.add(file));
                input.files = transfer.files;
            }catch(error){
                console.warn('Could not update the file picker on this browser', error);
            }
        }

        function appendOcrText(rawText) {
            const output = document.getElementById('ocr-output-text');
            if(!output) return;
            const text = (rawText || '').trim();
            if(!text) return;
            const current = (output.value || '').trim();
            const separator = current ? '\n\n--- FILE ---\n\n' : '';
            output.value = `${current}${separator}${text}`;
        }

        if(recommendBtn){
            recommendBtn.style.display = 'none';
            recommendBtn.addEventListener('click', ()=>{
                window.location.href = '/course';
            });
        }

        progressWrapEl = document.getElementById('progress-wrap');
        progressFillEl = document.getElementById('progress-fill');
        progressLabelEl = document.getElementById('progress-label');

        function setJourneyStep(activeStep){
            document.querySelectorAll('.journey-steps li').forEach(item => {
                const step = Number(item.dataset.step);
                item.classList.toggle('active', step === activeStep);
                item.classList.toggle('complete', step < activeStep);
            });
        }

        function extractSubjectEvidence(academicRecord){
            const evidence = [];
            const lines = String(academicRecord?.subjects || '').split(/\r?\n/).map(line => line.trim()).filter(Boolean);
            for(const line of lines){
                const match = line.match(/^(.+?)\s*[-:|]\s*(\d{1,3}(?:\.\d+)?)\s*$/);
                if(!match) continue;
                const grade = Number.parseFloat(match[2]);
                if(Number.isFinite(grade) && grade >= 0 && grade <= 100){
                    evidence.push({subject: match[1].trim(), grade});
                }
            }
            return evidence;
        }

        function subjectFieldLabel(subject){
            const label = String(subject || '').toLowerCase();
            const fields = [
                ['Math and quantitative subjects', ['math', 'algebra', 'calculus', 'statistics', 'trigonometry', 'geometry', 'probability']],
                ['Physics and physical sciences', ['physics', 'chemistry', 'earth science', 'physical science', 'geology', 'astronomy']],
                ['Biology and life sciences', ['biology', 'biological', 'life science', 'anatomy', 'physiology', 'botany', 'zoology', 'microbiology', 'biochemistry']],
                ['Physical education and fitness', ['physical education']],
                ['Health studies', ['health', 'nursing', 'medical', 'patient care', 'first aid', 'nutrition']],
                ['Environmental sciences', ['environment', 'agriculture', 'fisheries', 'ecology', 'ecosystem', 'forestry', 'crop', 'soil']],
                ['Technology subjects', ['technology', 'computer', 'programming', 'coding', 'software', 'ict', 'database', 'network', 'engineering', 'electronics', 'robotics']],
                ['Communication and languages', ['english', 'filipino', 'language', 'communication', 'speech', 'writing', 'literature', 'reading', 'oral']],
                ['Business subjects', ['business', 'accounting', 'management', 'marketing', 'economics', 'entrepreneur', 'finance', 'tourism', 'hospitality']],
                ['Social sciences and humanities', ['history', 'sociology', 'psychology', 'political', 'social', 'criminology', 'education', 'philosophy', 'governance', 'culture', 'humanities', 'religion']],
                ['Arts and creative subjects', ['art', 'design', 'music', 'media', 'creative', 'visual', 'physical education', 'performing']],
            ];
            return fields.find(([, terms]) => terms.some(term => label.includes(term)))?.[0] || '';
        }

        function renderInlineRecommendations(recommendations, academicRecord, comparisons = {}, shouldScroll = true){
            const section = document.getElementById('inline-recommendations');
            const list = document.getElementById('inline-recommendation-list');
            const courseAnalysis = document.getElementById('inline-course-analysis');
            const analytics = document.getElementById('recommendation-analytics');
            const dialog = document.getElementById('recommendation-dialog');
            const closeButton = document.getElementById('recommendation-dialog-close');
            if(!section || !list || !courseAnalysis || !analytics || !dialog || !closeButton) return;

            if(!dialog.hidden) document.body.style.overflow = dialog.dataset.previousOverflow || '';
            dialog.hidden = true;
            let activeCard = null;
            function closeDialog(){
                dialog.hidden = true;
                document.body.style.overflow = dialog.dataset.previousOverflow || '';
                activeCard?.focus();
            }
            closeButton.onclick = closeDialog;
            dialog.onclick = event => { if(event.target === dialog) closeDialog(); };
            dialog.onkeydown = event => {
                if(event.key === 'Escape') closeDialog();
                if(event.key === 'Tab'){
                    const focusable = [...dialog.querySelectorAll('button, [href], [tabindex]:not([tabindex="-1"])')];
                    const first = focusable[0];
                    const last = focusable[focusable.length - 1];
                    if(event.shiftKey && document.activeElement === first){ event.preventDefault(); last.focus(); }
                    else if(!event.shiftKey && document.activeElement === last){ event.preventDefault(); first.focus(); }
                }
            };

            const escapeHtml = value => String(value || '')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;');
            const items = (Array.isArray(recommendations) ? recommendations : [])
                .slice()
                .slice(0, configuredRecommendationLimit);
            const resultsTitle = document.getElementById('recommendation-results-title');
            if(resultsTitle){
                resultsTitle.textContent = `Your top ${items.length} course match${items.length === 1 ? '' : 'es'}`;
            }
            list.innerHTML = items.length
                ? items.map((item, index) => `
                    <article class="inline-recommendation-card${index === 0 ? ' best-match' : ''}" data-recommendation-index="${index}">
                        <div class="recommendation-rank">${String(index + 1).padStart(2, '0')}</div>
                        <div class="recommendation-detail">
                            <div class="recommendation-title-row">
                                <h3>${escapeHtml(item.course)}</h3>
                                ${index === 0 ? '<span class="best-match-badge"><i class="fa-solid fa-star"></i> Best match</span>' : ''}
                            </div>
                            <p>${escapeHtml(item.description || item.reason)}</p>
                            ${item.reason ? `<small>${escapeHtml(item.reason)}</small>` : ''}
                            ${(() => {
                                const fieldPriorities = new Map((Array.isArray(item.subject_field_evidence) ? item.subject_field_evidence : []).map(entry => [entry.field, entry.priority]));
                                const courseEvidence = extractSubjectEvidence(academicRecord)
                                    .map(entry => ({...entry, field: subjectFieldLabel(entry.subject)}))
                                    .filter(entry => fieldPriorities.has(entry.field))
                                    .map(entry => ({...entry, priority: fieldPriorities.get(entry.field)}));
                                const majorEvidence = courseEvidence.filter(entry => entry.priority === 'major').sort((left, right) => right.grade - left.grade).slice(0, 3);
                                const supportingEvidence = courseEvidence.filter(entry => entry.priority === 'supporting').sort((left, right) => right.grade - left.grade).slice(0, 3);
                                const renderEvidence = entries => entries.map(entry => `<b>${escapeHtml(entry.subject)} <em>${entry.grade.toFixed(0)}</em></b>`).join('');
                                return majorEvidence.length || supportingEvidence.length
                                    ? `<div class="subject-evidence">${majorEvidence.length ? `<span>Major related subjects</span><div>${renderEvidence(majorEvidence)}</div>` : ''}${supportingEvidence.length ? `<span>Supporting subjects</span><div>${renderEvidence(supportingEvidence)}</div>` : ''}</div>`
                                    : '';
                            })()}
                            <div class="match-meter" aria-label="${escapeHtml(item.course)} recommendation match ${Math.round(Number(item.match_score ?? item.confidence ?? 0))} percent">
                                <div class="match-meter-label"><span>Recommendation match</span><strong>${Math.round(Number(item.match_score ?? item.confidence ?? 0))}%</strong></div>
                                <div class="match-meter-track"><span style="width:${Math.max(0, Math.min(100, Number(item.match_score ?? item.confidence ?? 0)))}%"></span></div>
                            </div>
                            <div class="recommendation-actions">
                                <button class="recommendation-match-explanation" type="button" aria-haspopup="dialog">${index === 0 ? 'Why this is your best match' : 'Why this course matches'} <i class="fa-solid fa-lightbulb" aria-hidden="true"></i></button>
                            </div>
                        </div>
                    </article>
                `).join('')
                : '<p class="recommendation-empty">No course matches could be generated from these grades.</p>';

            function renderSelectedCourse(index){
                const item = items[index];
                if(!item) return;
                analytics.hidden = false;
                courseAnalysis.hidden = false;
                const comparison = comparisons[item.course] || {};
                const rows = Array.isArray(comparison.comparison) ? comparison.comparison : [];
                const studentAverage = Number(comparison.student_overall);
                const courseAverage = Number(comparison.course_average);
                const difference = Number(comparison.overall_gap);
                const hasStudentAverage = comparison.student_overall !== null && Number.isFinite(studentAverage);
                const hasCourseAverage = comparison.course_average !== null && Number.isFinite(courseAverage);
                const hasDifference = comparison.overall_gap !== null && Number.isFinite(difference);
                const fieldEvidence = Array.isArray(item.subject_field_evidence) ? item.subject_field_evidence : [];
                const fieldAverage = item.field_fit === null || item.field_fit === undefined ? null : Number(item.field_fit);
                const strandLabel = String(item.strand_label || '').trim();
                courseAnalysis.innerHTML = `
                    <div class="course-analysis-header">
                        <div><span>${index === 0 ? 'Your best match' : 'Recommended course'}</span><h3>${escapeHtml(item.course)}</h3></div>
                        <strong>${Math.round(Number(item.confidence || 0))}% profile similarity</strong>
                    </div>
                    <p class="course-analysis-reason"><i class="fa-solid fa-lightbulb"></i>${escapeHtml(item.reason || 'This program matches your academic profile and subject strengths.')}</p>
                    <p class="course-analysis-description">${escapeHtml(item.description || 'Course description is not available.')}</p>
                    <div class="course-analysis-signals" aria-label="Recommendation signals">
                        <div class="course-analysis-signal"><span>Grade-profile similarity</span><strong>${Math.round(Number(item.confidence || 0))}%</strong><small>Based on graded subject categories compared with this course profile.</small></div>
                        <div class="course-analysis-signal"><span>Related-field grades</span><strong>${fieldAverage !== null && Number.isFinite(fieldAverage) ? fieldAverage.toFixed(1) : 'Not available'}</strong><small>${fieldEvidence.length ? fieldEvidence.map(entry => `${escapeHtml(entry.field)} ${Number(entry.grade).toFixed(1)}`).join(' · ') : 'No recognized related-field grades.'}</small></div>
                        <div class="course-analysis-signal"><span>${strandLabel ? `${escapeHtml(strandLabel)} strand` : 'Strand'}</span><strong>${strandLabel ? (item.strand_alignment ? 'Aligned' : 'Not aligned') : 'Not provided'}</strong><small>Strand alignment helps prioritize results; it does not replace grade evidence.</small></div>
                    </div>
                    <div class="course-analysis-summary">
                        <div><span>Your average (graded categories)</span><strong>${hasStudentAverage ? studentAverage.toFixed(2) : 'N/A'}</strong></div>
                        <div><span>Course reference (same categories)</span><strong>${hasCourseAverage ? courseAverage.toFixed(2) : 'N/A'}</strong></div>
                        <div><span>Difference</span><strong class="${hasDifference && difference >= 0 ? 'analysis-up' : 'analysis-down'}">${hasDifference ? `${difference >= 0 ? '+' : ''}${difference.toFixed(2)}` : 'N/A'}</strong></div>
                    </div>
                    <div class="course-analysis-bars">
                        ${rows.map(row => `
                            <div class="analysis-subject">
                                <strong>${escapeHtml(String(row.subject || '').replace(/^./, letter => letter.toUpperCase()))}</strong>
                                ${row.student === null || row.student === undefined ? '<p class="analysis-missing-grade">No grade available; excluded from the overall comparison.</p>' : `<div class="analysis-series"><span>Your grade</span><div><i class="student-score" style="width:${Math.max(0, Math.min(100, Number(row.student)))}%"></i></div><b>${Number(row.student).toFixed(1)}</b></div>`}
                                <div class="analysis-series"><span>Reference</span><div><i class="course-score" style="width:${Math.max(0, Math.min(100, Number(row.course || 0)))}%"></i></div><b>${Number(row.course || 0).toFixed(1)}</b></div>
                            </div>
                        `).join('')}
                    </div>
                    <p class="course-analysis-note">${escapeHtml(comparison.narrative || 'Course reference profiles are guidance, not actual student averages or admission requirements.')}</p>
                    <p class="course-analysis-disclaimer">These similarity estimates are guidance, not admission probabilities. Course reference profiles are not actual student averages or admission requirements.</p>
                `;
                activeCard = list.querySelectorAll('.inline-recommendation-card')[index];
                dialog.dataset.previousOverflow = document.body.style.overflow;
                dialog.hidden = false;
                document.body.style.overflow = 'hidden';
                closeButton.focus();
            }

            function renderMatchExplanation(index){
                const item = items[index];
                if(!item) return;
                const fieldPriorities = new Map((Array.isArray(item.subject_field_evidence) ? item.subject_field_evidence : []).map(entry => [entry.field, entry.priority]));
                const supportingSubjects = extractSubjectEvidence(academicRecord)
                    .map(entry => ({...entry, field: subjectFieldLabel(entry.subject), priority: fieldPriorities.get(subjectFieldLabel(entry.subject))}))
                    .filter(entry => entry.priority)
                    .sort((left, right) => (left.priority === right.priority ? right.grade - left.grade : left.priority === 'major' ? -1 : 1))
                    .slice(0, 6);
                const strandLabel = String(item.strand_label || '').trim();
                const matchScore = Math.round(Number(item.match_score ?? item.confidence ?? 0));
                const profileSimilarity = Math.round(Number(item.confidence || 0));
                const subjectNames = [...new Set(supportingSubjects.slice(0, 4).map(entry => entry.subject))];
                const subjectList = subjectNames.length === 1
                    ? subjectNames[0]
                    : subjectNames.length === 2
                        ? `${subjectNames[0]} and ${subjectNames[1]}`
                        : `${subjectNames.slice(0, -1).join(', ')}, and ${subjectNames[subjectNames.length - 1]}`;
                const fieldFitExplanations = {
                    'Allied Health': 'Health programs build on science knowledge, careful observation, and understanding people\'s wellbeing.',
                    'Engineering': 'Engineering applies mathematics and physical-science problem solving to design and improve systems.',
                    'Computer Studies': 'Computing programs use logical thinking, technology, and structured problem solving.',
                    'Business & Management': 'Business programs use planning, communication, and financial decision-making.',
                    'Hospitality & Tourism': 'Tourism and hospitality work draws on communication, service, and understanding visitors\' needs.',
                    'Social Sciences & Education': 'These programs draw on communication and understanding people, communities, and learning.',
                    'Arts & Design': 'Creative programs use visual communication, design thinking, and expressive skills.',
                    'Sports & Physical Education': 'These programs combine physical performance, health knowledge, and instruction.',
                };
                const fieldExplanation = fieldFitExplanations[item.category] || 'This course overlaps with the strengths shown in your academic profile.';
                const fitExplanation = subjectList
                    ? `${index === 0 ? 'This is your strongest course match' : 'This course is a relevant match'} because your grades in ${subjectList} show strengths related to ${item.course}. ${fieldExplanation}${strandLabel && item.strand_alignment ? ` It is also listed for your ${strandLabel} strand.` : !strandLabel ? ' No strand was identified, so the match is based on your grade profile.' : ''}`
                    : `${index === 0 ? 'This is currently your strongest course match' : 'This course is a relevant match'} based on how your overall grade profile compares with ${item.course}. ${fieldExplanation}${strandLabel && item.strand_alignment ? ` It is also listed for your ${strandLabel} strand.` : ''}`;

                courseAnalysis.innerHTML = `
                    <div class="course-fit-explanation">
                        <div class="course-analysis-header">
                            <div><span>${index === 0 ? 'Why this is your best match' : 'Why this course matches'}</span><h3>${escapeHtml(item.course)}</h3></div>
                            <strong>${matchScore}% match</strong>
                        </div>
                        <p class="course-analysis-reason course-fit-narrative"><i class="fa-solid fa-lightbulb"></i>${escapeHtml(fitExplanation)}</p>
                        <div class="course-analysis-signals" aria-label="Why this course matches">
                            <div class="course-analysis-signal"><span>Recommendation match</span><strong>${matchScore}%</strong><small>Final ranking score, including course relevance signals.</small></div>
                            <div class="course-analysis-signal"><span>Grade-profile similarity</span><strong>${profileSimilarity}%</strong><small>Similarity between your grades and the course reference profile.</small></div>
                            <div class="course-analysis-signal"><span>${strandLabel ? `${escapeHtml(strandLabel)} strand` : 'Strand'}</span><strong>${strandLabel ? (item.strand_alignment ? 'Aligned' : 'Not aligned') : 'Not provided'}</strong><small>${strandLabel ? 'Only courses tagged for the selected strand are eligible.' : 'No strand was identified; matches are ranked from your strongest grades.'}</small></div>
                        </div>
                        <p class="course-analysis-disclaimer">This is a relative academic match, not an admission probability or eligibility decision.</p>
                    </div>
                `;
                courseAnalysis.hidden = false;
                analytics.hidden = true;
                activeCard = list.querySelectorAll('.recommendation-match-explanation')[index];
                dialog.dataset.previousOverflow = document.body.style.overflow;
                dialog.hidden = false;
                document.body.style.overflow = 'hidden';
                closeButton.focus();
            }

            list.querySelectorAll('.recommendation-match-explanation').forEach((button, index) => {
                button.addEventListener('click', () => renderMatchExplanation(index));
            });
            list.querySelectorAll('.inline-recommendation-card').forEach((card, index) => {
                card.tabIndex = 0;
                card.setAttribute('role', 'button');
                card.setAttribute('aria-haspopup', 'dialog');
                card.setAttribute('aria-label', `View academic comparison for ${items[index]?.course || 'this course'}`);
                card.addEventListener('click', event => {
                    if(event.target.closest('button, a')) return;
                    renderSelectedCourse(index);
                });
                card.addEventListener('keydown', event => {
                    if(event.target !== card || (event.key !== 'Enter' && event.key !== ' ')) return;
                    event.preventDefault();
                    renderSelectedCourse(index);
                });
            });
            const gradeValues = String(academicRecord?.grades || '')
                .split(/[\s,;|]+/)
                .map(value => Number.parseFloat(value))
                .filter(value => Number.isFinite(value) && value >= 0 && value <= 100);
            const subjectLines = String(academicRecord?.subjects || '')
                .split(/\r?\n/)
                .map(value => value.trim())
                .filter(Boolean);
            const average = gradeValues.length
                ? gradeValues.reduce((total, grade) => total + grade, 0) / gradeValues.length
                : Number.parseFloat(academicRecord?.gwa);
            const spread = gradeValues.length > 1
                ? Math.max(...gradeValues) - Math.min(...gradeValues)
                : null;
            const evidenceCount = Math.max(gradeValues.length, subjectLines.length);
            const topConfidence = Number(items[0]?.confidence || 0);
            const evidenceQuality = evidenceCount >= 6 && topConfidence >= 60
                ? 'Strong'
                : evidenceCount >= 3 && topConfidence >= 35
                    ? 'Moderate'
                    : 'Limited';

            analytics.innerHTML = items.length ? `
                <div class="analytics-heading">
                    <div><span>Recommendation evidence</span><h3>How this result was calculated</h3></div>
                    <span class="evidence-quality evidence-${evidenceQuality.toLowerCase()}">Evidence quality: ${evidenceQuality}</span>
                    <p>These indicators summarize the academic data used by the matching model. They are guidance, not an admission guarantee.</p>
                </div>
                <div class="analytics-grid">
                    <div class="analytics-stat"><i class="fa-solid fa-book-open"></i><strong>${evidenceCount}</strong><span>subjects analyzed</span></div>
                    <div class="analytics-stat"><i class="fa-solid fa-chart-line"></i><strong>${Number.isFinite(average) ? average.toFixed(1) : 'N/A'}</strong><span>average grade</span></div>
                    <div class="analytics-stat"><i class="fa-solid fa-arrows-left-right"></i><strong>${spread === null ? 'N/A' : spread.toFixed(1)}</strong><span>grade-point spread</span></div>
                </div>
            ` : '';
            section.style.display = 'block';
            setJourneyStep(4);
            if(shouldScroll) section.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }

        function setSavedProfileView(saved){
            const uploadArea = document.getElementById('ocr-upload-area');
            const reviewPanel = document.getElementById('ocr-review-panel');
            if(uploadArea) uploadArea.hidden = saved;
            if(saved && reviewPanel) reviewPanel.style.display = 'none';
        }

        window.restoreSavedProfileView = data => {
            if(!data || !Array.isArray(data.recommendation) || !data.recommendation.length) return;
            renderInlineRecommendations(
                data.recommendation,
                { subjects: data.subjects || '', gwa: data.gwa || '' },
                data.comparisons || {},
                false
            );
            setSavedProfileView(true);
        };

        window.showStudentUpload = () => {
            setSavedProfileView(false);
            document.getElementById('inline-recommendations').style.display = 'none';
            document.getElementById('dropzone')?.scrollIntoView({behavior:'smooth',block:'center'});
        }

        function resetRecommendationFlow(){
            input.value = '';
            queuedFiles = [];
            selectedFileIndex = null;
            updatePreviewForFiles([]);
            setUploadMessage('');
            hideProgress();
            window.latestDoclingTable = [];
            window.latestParsedSubjects = [];
            window.latestOcrReviewSubjects = [];
            window.latestOcrReviewRequired = false;
            const panel = document.getElementById('ocr-review-panel');
            const form = document.getElementById('ocr-review-form');
            const results = document.getElementById('inline-recommendations');
            window.latestStudentMetadata = window.latestSavedProfile?.success ? {
                studentNumber: window.latestSavedProfile.studentNumber || '',
                firstName: window.latestSavedProfile.firstName || '',
                middleInitial: window.latestSavedProfile.middleInitial || '',
                lastName: window.latestSavedProfile.lastName || '',
                strand: window.latestSavedProfile.strand || ''
            } : {};
            updateOcrIdentityPreview(window.latestStudentMetadata);
            if(panel) panel.style.display = 'none';
            if(form) form.innerHTML = '';
            if(results) results.style.display = 'none';
            setSavedProfileView(false);
            const identityCard = document.getElementById('ocr-identity-status');
            if(identityCard){
                identityCard.querySelector('.review-section-title').textContent = 'Student details are read from your report card';
                identityCard.querySelector('p').textContent = 'OCR will extract the student number, first name, middle initial, last name, and SHS strand or track together with the subjects and grades.';
            }
            setJourneyStep(1);
            document.getElementById('dropzone')?.scrollIntoView({behavior:'smooth',block:'center'});
        }

        document.getElementById('upload-another')?.addEventListener('click', resetRecommendationFlow);

        async function updatePreviewForFiles(files){
            const previewWrap = previewContainer;
            if(!previewWrap) return;
            previewWrap.innerHTML = '';
            const selectedFiles = Array.from(files || []);
            if(fileActions) fileActions.hidden = selectedFiles.length === 0;
            if(!selectedFiles.length){
                setUploadMessage('');
                return;
            }
            setUploadMessage(`${selectedFiles.length} file${selectedFiles.length === 1 ? '' : 's'} ready to process.`);
            for(const [fileIndex, file] of selectedFiles.entries()){
                const fileName = (file.name || '').toLowerCase();
                const tile = document.createElement('div');
                tile.className = 'selected-file';
                tile.classList.toggle('selected-for-replacement', selectedFileIndex === fileIndex);
                tile.setAttribute('role', 'button');
                tile.setAttribute('tabindex', '0');
                tile.setAttribute('aria-label', `Select ${file.name} to replace`);
                tile.addEventListener('click', event => {
                    event.stopPropagation();
                    if(event.target.closest('.remove-file')) return;
                    selectedFileIndex = selectedFileIndex === fileIndex ? null : fileIndex;
                    updatePreviewForFiles(parseFilesFromInput(input.files));
                });
                tile.addEventListener('keydown', event => {
                    if(event.key === 'Enter' || event.key === ' '){
                        event.preventDefault();
                        selectedFileIndex = selectedFileIndex === fileIndex ? null : fileIndex;
                        updatePreviewForFiles(parseFilesFromInput(input.files));
                    }
                });
                const isImage = (file.type && file.type.startsWith('image/')) || /\.(jpg|jpeg|png|gif|bmp|webp)$/i.test(fileName);
                const media = document.createElement('div');
                media.className = 'selected-file-media';
                if(isImage){
                    const image = document.createElement('img');
                    image.src = URL.createObjectURL(file);
                    image.alt = file.name;
                    media.appendChild(image);
                }else if(fileName.endsWith('.pdf')){
                    try{
                        await loadScript('https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.min.js');
                        const pdfjsLib = window['pdfjsLib'];
                        if(pdfjsLib && pdfjsLib.GlobalWorkerOptions){
                            pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.worker.min.js';
                        }
                        const pdf = await pdfjsLib.getDocument({data: await file.arrayBuffer()}).promise;
                        const page = await pdf.getPage(1);
                        const viewport = page.getViewport({scale: 0.35});
                        const canvas = document.createElement('canvas');
                        canvas.width = viewport.width;
                        canvas.height = viewport.height;
                        await page.render({canvasContext: canvas.getContext('2d'), viewport}).promise;
                        media.appendChild(canvas);
                    }catch(_){
                        media.innerHTML = '<i class="fa-solid fa-file-pdf"></i>';
                    }
                }else{
                    media.innerHTML = '<i class="fa-solid fa-file-lines"></i>';
                }
                const details = document.createElement('div');
                details.className = 'selected-file-details';
                const label = document.createElement('strong');
                label.textContent = file.name;
                const size = document.createElement('span');
                size.textContent = `${Math.max(1, Math.round(file.size / 1024))} KB`;
                details.append(label, size);
                const remove = document.createElement('button');
                remove.type = 'button';
                remove.className = 'remove-file';
                remove.setAttribute('aria-label', `Remove ${file.name}`);
                remove.title = 'Remove file';
                remove.innerHTML = '<i class="fa-solid fa-xmark"></i>';
                remove.addEventListener('click', event => {
                    event.stopPropagation();
                    const remaining = queuedFiles.filter((_, index) => index !== fileIndex);
                    if(selectedFileIndex === fileIndex) selectedFileIndex = null;
                    else if(selectedFileIndex !== null && fileIndex < selectedFileIndex) selectedFileIndex -= 1;
                    setInputFiles(remaining);
                    queuedFiles = remaining;
                    updatePreviewForFiles(remaining);
                });
                tile.append(media, details, remove);
                previewWrap.appendChild(tile);
            }
        }

        input.addEventListener('change', (e)=>{
            setUploadMessage('');
            const selectedInputFiles = parseFilesFromInput(e.target.files);
            const unsupportedFiles = selectedInputFiles.filter(file => !isSupportedReportCardFile(file));
            if(unsupportedFiles.length){
                setUploadMessage('Use JPG, JPEG, PNG, WEBP, PDF, or DOCX report-card files.', 'error');
                input.value = '';
                return;
            }
            const incomingFiles = selectedInputFiles;
            selectedFileIndex = null;
            const existingKeys = new Set(queuedFiles.map(file => `${file.name}|${file.size}|${file.lastModified}`));
            const addedFiles = incomingFiles.filter(file => {
                const key = `${file.name}|${file.size}|${file.lastModified}`;
                if(existingKeys.has(key)) return false;
                existingKeys.add(key);
                return true;
            });
            queuedFiles = [...queuedFiles, ...addedFiles].slice(0, 10);
            setInputFiles(queuedFiles);
            updatePreviewForFiles(queuedFiles);
        });

        replaceFilesBtn?.addEventListener('click', () => input.click());
        clearFilesBtn?.addEventListener('click', () => {
            input.value = '';
            selectedFileIndex = null;
            queuedFiles = [];
            updatePreviewForFiles([]);
        });

        function assignFilesToInput(fileList){
            const files = parseFilesFromInput(fileList);
            if(!files.length) return [];
            try{
                const dt = new DataTransfer();
                files.forEach(file => dt.items.add(file));
                input.files = dt.files;
            }catch(e){
                console.warn('Could not assign dropped files to input element', e);
            }
            queuedFiles = files;
            updatePreviewForFiles(files);
            return files;
        }

        function renderOcrReviewPanel(student){
            const panel = document.getElementById('ocr-review-panel');
            const form = document.getElementById('ocr-review-form');
            if(!panel || !form) return;

            const record = student || {
                strand: '',
                gwa: '',
                grades: '',
                subjects: ''
            };

            const reviewedStorageKey = (() => {
                const source = `${record.subjects || ''}|${record.grades || ''}`;
                let hash = 0;
                for(let index = 0; index < source.length; index++) hash = (hash * 31 + source.charCodeAt(index)) | 0;
                return `pathfinder-reviewed-grades:${hash}`;
            })();

            function savedReviewRows(){
                try{
                    const saved = JSON.parse(sessionStorage.getItem(reviewedStorageKey) || 'null');
                    return Array.isArray(saved) && saved.length ? saved : null;
                }catch{
                    return null;
                }
            }

            function initialReviewRows(){
                const savedRows = savedReviewRows();
                if(savedRows) return savedRows.map(row => ({subject: String(row.subject || ''), grade: String(row.grade ?? ''), needsReview: false}));
                const subjectLines = String(record.subjects || '').split(/\r?\n/).map(value => value.trim()).filter(Boolean);
                const gradeValues = String(record.grades || '').split(/[\s,;|]+/).map(value => value.trim()).filter(Boolean);
                const reviewSubjects = Array.isArray(record.reviewSubjects) ? record.reviewSubjects : [];
                const normalizeSubject = value => String(value || '')
                    .replace(/^\s*\d{3,6}\s+[A-Za-z0-9]{1,10}\s+/, '')
                    .toLowerCase()
                    .replace(/[^a-z0-9]+/g, ' ')
                    .trim();
                const matchedReviewIndexes = new Set();
                const rows = subjectLines.map((line, index) => {
                    const combined = line.match(/^(.+?)\s*[-:|]\s*(\d{1,3}(?:\.\d+)?)\s*$/);
                    const subject = combined ? combined[1].trim() : line;
                    const grade = combined ? combined[2] : (gradeValues[index] || '');
                    const normalizedSubject = normalizeSubject(subject);
                    const review = reviewSubjects.find((item, reviewIndex) => {
                        const normalizedReviewSubject = normalizeSubject(item.subject_name);
                        const sameSubject = normalizedReviewSubject === normalizedSubject
                            || normalizedReviewSubject.startsWith(`${normalizedSubject} `)
                            || normalizedSubject.startsWith(`${normalizedReviewSubject} `);
                        const sameGrade = item.grade === null || item.grade === undefined || !grade || Number(item.grade) === Number(grade);
                        if(sameSubject && sameGrade) matchedReviewIndexes.add(reviewIndex);
                        return sameSubject && sameGrade;
                    });
                    return {
                        subject,
                        grade,
                        needsReview: Boolean(review),
                        reviewReason: review?.reason || review?.errors?.[0] || 'OCR marked this subject grade for review.',
                    };
                });
                const knownSubjectNames = new Set(rows.map(row => normalizeSubject(row.subject)).filter(Boolean));
                const appendedReviewSubjects = new Set();
                const missingReviewRows = reviewSubjects
                    .filter((item, reviewIndex) => item?.needs_review && item.subject_name && !matchedReviewIndexes.has(reviewIndex))
                    .filter(item => {
                        const key = normalizeSubject(item.subject_name);
                        if(appendedReviewSubjects.has(key)) return false;
                        appendedReviewSubjects.add(key);
                        return true;
                    })
                    .map(item => ({
                        subject: item.subject_name,
                        grade: item.grade === null || item.grade === undefined ? '' : String(item.grade),
                        needsReview: true,
                        reviewReason: item.reason || item.errors?.[0] || 'OCR marked this subject grade for review.',
                    }));
                missingReviewRows.forEach(row => knownSubjectNames.add(normalizeSubject(row.subject)));
                const mergedTableReviewRows = [];
                const rawRows = Array.isArray(record.rawTable) ? record.rawTable : [];
                const courseCodePattern = /\b\d{3,6}\s+[A-Z0-9]{2,8}\b/gi;
                for(const rawRow of rawRows){
                    for(const cell of Array.isArray(rawRow?.cells) ? rawRow.cells : []){
                        const text = String(cell?.text || '').replace(/\s+/g, ' ').trim();
                        const courseCodes = [...text.matchAll(courseCodePattern)];
                        if(courseCodes.length < 2) continue;
                        for(let index = 0; index < courseCodes.length - 1; index++){
                            const start = courseCodes[index].index + courseCodes[index][0].length;
                            const end = courseCodes[index + 1].index;
                            const subject = text.slice(start, end).trim();
                            const normalizedSubject = normalizeSubject(subject);
                            if(!subject || !/[a-z]{3}/i.test(subject) || knownSubjectNames.has(normalizedSubject)) continue;
                            knownSubjectNames.add(normalizedSubject);
                            mergedTableReviewRows.push({
                                subject,
                                grade: '',
                                needsReview: true,
                                reviewReason: 'This subject appeared in a merged report-card row, but its grade could not be matched. Verify it against the report card.',
                            });
                        }
                    }
                }
                return [...rows, ...missingReviewRows, ...mergedTableReviewRows];
            }

            function reviewRows(){
                return [...document.querySelectorAll('.review-subject-row')].map(row => ({
                    subject: row.querySelector('.review-subject-input')?.value.trim() || '',
                    grade: row.querySelector('.review-grade-input')?.value.trim() || '',
                    needsReview: row.dataset.needsReview === 'true',
                }));
            }

            function reviewAverage(rows = reviewRows()){
                const grades = rows
                    .map(row => Number.parseFloat(row.grade))
                    .filter(grade => Number.isFinite(grade) && grade >= 0 && grade <= 100);
                return grades.length ? grades.reduce((total, grade) => total + grade, 0) / grades.length : null;
            }

            function updateReviewAverage(){
                const average = reviewAverage();
                const output = document.getElementById('review-average');
                if(output) output.textContent = average === null ? 'N/A' : average.toFixed(2);
            }

            function addReviewRow(subject = '', grade = '', review = null){
                const body = document.getElementById('review-subject-body');
                if(!body) return;
                const row = document.createElement('tr');
                row.className = 'review-subject-row';
                row.innerHTML = `
                    <td><input class="review-subject-input" type="text" aria-label="Subject name" value="${String(subject).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')}" placeholder="Subject name"></td>
                    <td><input class="review-grade-input" type="number" aria-label="Grade" value="${String(grade).replace(/"/g, '&quot;')}" min="0" max="100" step="0.01" placeholder="Grade"></td>
                    <td><button class="review-remove-row" type="button" aria-label="Remove subject row" title="Remove row"><i class="fa-solid fa-xmark"></i></button></td>
                `;
                if(review?.needsReview){
                    row.dataset.needsReview = 'true';
                    row.classList.add('review-subject-row-flagged');
                    const notice = document.createElement('small');
                    notice.className = 'review-subject-flag';
                    notice.textContent = `Needs review: ${review.reviewReason}`;
                    row.querySelector('td')?.appendChild(notice);
                }
                row.querySelectorAll('input').forEach(inputElement => inputElement.addEventListener('input', () => {
                    row.classList.remove('review-subject-row-invalid');
                    markReviewUnsaved();
                    updateReviewAverage();
                    updateReviewWarnings();
                }));
                row.querySelector('.review-remove-row')?.addEventListener('click', () => {
                    row.remove();
                    markReviewUnsaved();
                    updateReviewAverage();
                    updateReviewWarnings();
                });
                body.appendChild(row);
            }

            function markReviewUnsaved(){
                const button = document.getElementById('save-review-grades');
                if(!button || !button.classList.contains('is-saved')) return;
                button.classList.remove('is-saved');
                button.innerHTML = '<i class="fa-solid fa-floppy-disk"></i> Save grades';
            }

            function saveReviewGrades(){
                const button = document.getElementById('save-review-grades');
                const warningBox = document.getElementById('review-quality-warning');
                const rowElements = [...document.querySelectorAll('.review-subject-row')];
                const invalidRows = [];
                const savedRows = [];
                rowElements.forEach(row => {
                    const subject = row.querySelector('.review-subject-input')?.value.trim() || '';
                    const gradeText = row.querySelector('.review-grade-input')?.value.trim() || '';
                    if(!subject && !gradeText) return;
                    const grade = Number.parseFloat(gradeText);
                    const valid = subject && gradeText && Number.isFinite(grade) && grade >= 0 && grade <= 100;
                    row.classList.toggle('review-subject-row-invalid', !valid);
                    if(valid) savedRows.push({subject, grade: gradeText});
                    else invalidRows.push(row);
                });
                if(invalidRows.length || !savedRows.length){
                    if(warningBox){
                        warningBox.classList.remove('review-quality-ok');
                        warningBox.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i><div><strong>Grades not saved</strong><p>${savedRows.length ? `Fix the ${invalidRows.length} highlighted row${invalidRows.length === 1 ? '' : 's'}: each subject needs a name and a grade from 0 to 100.` : 'Add at least one subject with a grade before saving.'}</p></div>`;
                    }
                    invalidRows[0]?.querySelector('input')?.focus();
                    return;
                }
                try{
                    sessionStorage.setItem(reviewedStorageKey, JSON.stringify(savedRows));
                }catch(error){
                    console.warn('Could not persist reviewed grades', error);
                }
                rowElements.forEach(row => {
                    delete row.dataset.needsReview;
                    row.classList.remove('review-subject-row-flagged');
                    row.querySelector('.review-subject-flag')?.remove();
                });
                window.latestOcrReviewRequired = false;
                updateReviewAverage();
                updateReviewWarnings();
                if(button){
                    button.classList.add('is-saved');
                    button.innerHTML = '<i class="fa-solid fa-check"></i> Grades saved';
                }
            }

            function reviewWarnings(){
                const rows = reviewRows();
                const grades = rows.map(row => Number.parseFloat(row.grade)).filter(Number.isFinite);
                const subjectLines = rows.map(row => row.subject).filter(Boolean);
                const warnings = [];
                const flaggedSubjects = [...new Set(rows.filter(row => row.needsReview).map(row => row.subject).filter(Boolean))];
                if(flaggedSubjects.length) warnings.push(`OCR marked these subjects for review: ${flaggedSubjects.join(', ')}.`);
                else if(window.latestOcrReviewRequired) warnings.push('OCR needs review but did not identify a specific subject. Compare each extracted row with the report card.');
                if(!subjectLines.length) warnings.push('No subject names were detected.');
                if(!grades.length) warnings.push('No numeric grades were detected.');
                const incompleteRows = rows.filter(row => !row.subject || !row.grade);
                if(incompleteRows.length) warnings.push(`${incompleteRows.length} row${incompleteRows.length === 1 ? ' is' : 's are'} incomplete.`);
                const unusualGrades = grades.filter(grade => grade < 75 || grade > 99);
                if(unusualGrades.length) warnings.push(`${unusualGrades.length} grade${unusualGrades.length === 1 ? '' : 's'} fall outside the expected 75â€“99 range.`);
                return warnings;
            }

            function updateReviewWarnings(){
                const warningBox = document.getElementById('review-quality-warning');
                if(!warningBox) return;
                const warnings = reviewWarnings();
                warningBox.innerHTML = warnings.length
                    ? `<i class="fa-solid fa-triangle-exclamation"></i><div><strong>Check the extracted data</strong><ul>${warnings.map(warning => `<li>${warning}</li>`).join('')}</ul></div>`
                    : '<i class="fa-solid fa-circle-check"></i><div><strong>Extraction looks complete</strong><p>Review the values once more before generating recommendations.</p></div>';
                warningBox.classList.toggle('review-quality-ok', warnings.length === 0);
            }

            form.innerHTML = `
                <div class="review-form-grid">
                    <div class="review-section-title">Extracted academic details</div>
                    <div id="review-quality-warning" class="review-quality-warning" role="status" aria-live="polite"></div>
                    <div class="review-average-card">
                        <span>Computed average</span>
                        <strong id="review-average">N/A</strong>
                        <small>Automatically updates from the valid grades below.</small>
                    </div>
                    <div class="review-table-wrap">
                        <table class="review-subject-table">
                            <thead><tr><th>Subject</th><th>Grade</th><th><span class="sr-only">Actions</span></th></tr></thead>
                            <tbody id="review-subject-body"></tbody>
                        </table>
                    </div>
                    <button id="add-review-row" type="button" class="add-review-row"><i class="fa-solid fa-plus"></i> Add subject</button>
                    <div class="review-actions">
                        <button id="save-reviewed-record" type="button" class="review-primary-btn"><i class="fa-solid fa-wand-magic-sparkles"></i> ${isGuest ? 'Show recommendations' : 'Save and show recommendations'}</button>
                        <button id="save-review-grades" type="button" class="review-save-btn${savedReviewRows() ? ' is-saved' : ''}">${savedReviewRows() ? '<i class="fa-solid fa-check"></i> Grades saved' : '<i class="fa-solid fa-floppy-disk"></i> Save grades'}</button>
                        <button id="discard-reviewed-record" type="button" class="review-secondary-btn">Discard</button>
                    </div>
                </div>
            `;

            const saveBtn = document.getElementById('save-reviewed-record');
            const discardBtn = document.getElementById('discard-reviewed-record');
            const rows = initialReviewRows();
            (rows.length ? rows : [{subject:'', grade:''}]).forEach(row => addReviewRow(row.subject, row.grade, row));
            document.getElementById('add-review-row')?.addEventListener('click', () => {
                addReviewRow();
                markReviewUnsaved();
                document.querySelector('.review-subject-row:last-child .review-subject-input')?.focus();
                updateReviewWarnings();
            });
            document.getElementById('save-review-grades')?.addEventListener('click', saveReviewGrades);
            updateReviewAverage();
            updateReviewWarnings();

            saveBtn && (saveBtn.onclick = async () => {
                const reviewedRows = reviewRows().filter(row => row.subject || row.grade);
                const average = reviewAverage(reviewedRows);
                const identity = window.latestStudentMetadata || {};
                const payload = {
                    studentNumber: identity.studentNumber || '',
                    firstName: identity.firstName || '',
                    middleInitial: identity.middleInitial || '',
                    lastName: identity.lastName || '',
                    strand: identity.strand || '',
                    gwa: average === null ? '' : average.toFixed(2),
                    grades: reviewedRows.map(row => row.grade).filter(Boolean).join(', '),
                    subjects: reviewedRows.filter(row => row.subject && row.grade).map(row => `${row.subject} - ${row.grade}`).join('\n')
                    ,ocrTable: window.latestDoclingTable || []
                };

                try{
                    setProgress(null, isGuest ? 'Preparing recommendations...' : 'Saving report card...');
                    const response = await fetch(isGuest ? '/api/v1/recommend_anonymous' : '/api/v1/save_profile', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const responseText = await response.text();
                    let data;
                    try {
                        data = responseText ? JSON.parse(responseText) : {};
                    } catch {
                        throw new Error(`The server returned an unreadable response (HTTP ${response.status}). Check the server logs.`);
                    }
                    if(!response.ok){
                        throw new Error((data && data.message) || `Request failed (HTTP ${response.status}).`);
                    }
                    if(data && data.success){
                        setProgress(100, isGuest ? 'Recommendations ready.' : 'Report card saved and recommendations ready.');
                        renderInlineRecommendations(data.recommendation, payload, data.comparisons);
                        setSavedProfileView(true);
                        panel.style.display = 'none';
                        form.innerHTML = '';
                    }else{
                        throw new Error((data && data.message) || 'Failed to save profile');
                    }
                }catch(error){
                    console.error('Save reviewed report failed:', error);
                    setProgress(0, 'Save failed');
                    alert('Save failed: ' + (error && error.message ? error.message : error));
                } finally {
                    setTimeout(hideProgress, 1600);
                }
            });

            discardBtn && (discardBtn.onclick = () => {
                try{ sessionStorage.removeItem(reviewedStorageKey); }catch{}
                panel.style.display = 'none';
                form.innerHTML = '';
            });

            panel.style.display = 'block';
            setJourneyStep(3);
            panel.scrollIntoView({behavior:'smooth',block:'start'});
        }

        if(dropzone){
            dropzone.addEventListener('click', (e)=>{
                if(e.target === input) return;
                input.click();
            });

            dropzone.addEventListener('keydown', event => {
                if(event.key === 'Enter' || event.key === ' '){
                    event.preventDefault();
                    input.click();
                }
            });

            ['dragenter','dragover'].forEach(evt=>{
                dropzone.addEventListener(evt, (e)=>{
                    e.preventDefault();
                    e.stopPropagation();
                    dropzone.classList.add('dragover');
                });
            });

            ['dragleave','dragend'].forEach(evt=>{
                dropzone.addEventListener(evt, (e)=>{
                    e.preventDefault();
                    e.stopPropagation();
                    dropzone.classList.remove('dragover');
                });
            });

            dropzone.addEventListener('drop', (e)=>{
                e.preventDefault();
                e.stopPropagation();
                dropzone.classList.remove('dragover');
                const files = e.dataTransfer && e.dataTransfer.files;
                const file = assignFilesToInput(files);
                if(file && runBtn && !runBtn.disabled){
                    runBtn.click();
                }
            });
        }

        runBtn && runBtn.addEventListener('click', async ()=>{
            const files = parseFilesFromInput(input.files);
            if(!files.length){ setUploadMessage('Choose at least one report card file first.', 'error'); dropzone?.focus(); return; }
            if(!consentCheckbox || !consentCheckbox.checked){ setUploadMessage('Read and accept the privacy notice before uploading.', 'error'); return; }

            runBtn.disabled = true;
            runBtn.setAttribute('aria-busy', 'true');
            setUploadMessage('');
            setJourneyStep(2);
            if(recommendBtn) recommendBtn.style.display = 'none';
            setProgress(null, 'Processing files with high-accuracy OCR...');

            try{
                let processedCount = 0;
                const total = files.length;
                const combinedSubjects = [];
                const combinedGrades = [];
                let combinedGwaTotal = 0;
                let combinedGwaCount = 0;
                const combinedTable = [];
                const savedIdentity = window.latestSavedProfile?.success ? {
                    studentNumber: window.latestSavedProfile.studentNumber || '',
                    firstName: window.latestSavedProfile.firstName || '',
                    middleInitial: window.latestSavedProfile.middleInitial || '',
                    lastName: window.latestSavedProfile.lastName || '',
                    strand: window.latestSavedProfile.strand || ''
                } : {};
                let combinedIdentity = savedIdentity;
                window.latestDoclingTable = [];
                window.latestStudentMetadata = {...savedIdentity};
                window.latestOcrReviewSubjects = [];
                window.latestOcrReviewRequired = false;
                window.latestAutomaticOcrDisabled = false;

                for (let i = 0; i < files.length; i++) {
                    const file = files[i];
                    if(!isSupportedReportCardFile(file)){
                        throw new Error('Use a JPG, JPEG, PNG, WEBP, PDF, or DOCX report-card file.');
                    }
                    setProgress(Math.round((i / total) * 100), `Processing file ${i + 1}/${total}: ${file.name}`);

                    const fileName = String(file.name || '').toLowerCase();
                    const rawText = fileName.endsWith('.pdf')
                        ? await extractTextFromPDF(file)
                        : fileName.endsWith('.docx')
                            ? await extractTextFromDocx(file)
                            : await imageBlobToText(file);
                    if(window.latestAutomaticOcrDisabled){
                        processedCount += 1;
                        continue;
                    }
                    if(!rawText || !rawText.trim()){
                        throw new Error(`No readable text was found in ${file.name}.`);
                    }
                    if(i === 0 && !savedIdentity.studentNumber && !savedIdentity.firstName && !savedIdentity.lastName){
                        combinedIdentity = {...(window.latestStudentMetadata || {})};
                        updateOcrIdentityPreview(combinedIdentity);
                    }
                    if(Array.isArray(window.latestDoclingTable)){
                        if(!combinedTable.length){
                            combinedTable.push(...window.latestDoclingTable);
                        }else{
                            combinedTable.push(...window.latestDoclingTable.slice(1));
                        }
                    }
                    const parsed = parseAcademicRecord(rawText || '');
                    if(parsed){
                        processedCount += 1;
                        if(rawText) appendOcrText(rawText);
                        if(Array.isArray(parsed.subjects)){
                            combinedSubjects.push(...parsed.subjects);
                        }else if(parsed.subjects){
                            combinedSubjects.push(...String(parsed.subjects).split(/\r?\n/).filter(Boolean));
                        }
                        if(parsed.grades){
                            combinedGrades.push(...String(parsed.grades).split(',').map(value => value.trim()).filter(Boolean));
                        }
                        const numericGwa = Number.parseFloat(parsed.gwa);
                        if(Number.isFinite(numericGwa)){
                            combinedGwaTotal += numericGwa;
                            combinedGwaCount += 1;
                        }
                    } else {
                        alert('The OCR service could not read the uploaded file. Please review the extracted content and save manually if needed.');
                    }
                }

                if(processedCount > 0){
                    window.latestStudentMetadata = combinedIdentity;
                    updateOcrIdentityPreview(window.latestStudentMetadata);
                    window.latestDoclingTable = combinedTable;
                    renderOcrReviewPanel({
                        gwa: combinedGwaCount ? (combinedGwaTotal / combinedGwaCount).toFixed(2) : '',
                        grades: combinedGrades.join(', '),
                        subjects: [...new Set(combinedSubjects)].join('\n'),
                        reviewSubjects: window.latestOcrReviewSubjects,
                        rawTable: combinedTable,
                    });
                    setProgress(100, window.latestAutomaticOcrDisabled
                        ? 'Automatic OCR is disabled. Enter the report details manually, then save.'
                        : `Processed ${processedCount}/${total} file${total > 1 ? 's' : ''} with high-accuracy OCR. Review and save.`);
                }else{
                    setProgress(0, 'No report cards could be parsed.');
                    setUploadMessage('No readable subject and grade rows were found. Try a clearer image or another file.', 'error');
                }
            }catch(e){
                console.error('Processing error', e);
                setProgress(0, 'Processing failed');
                setUploadMessage('Processing failed: ' + (e && e.message || e), 'error');
            }finally{
                runBtn.disabled = false;
                runBtn.removeAttribute('aria-busy');
                setTimeout(hideProgress, 2200);
            }
        });
    });
})();


function loadSavedProfile() {
    return fetch('/api/v1/get_profile')
        .then(response => response.json())
        .then(data => {
            if (!data.success) return data;

            window.latestSavedProfile = data;

            window.latestStudentMetadata = {
                studentNumber: data.studentNumber || '',
                firstName: data.firstName || '',
                middleInitial: data.middleInitial || '',
                lastName: data.lastName || '',
                strand: data.strand || ''
            };
            updateOcrIdentityPreview(window.latestStudentMetadata);
            const identityCard = document.getElementById('ocr-identity-status');
            if(identityCard){
                identityCard.querySelector('.review-section-title').textContent = 'Saved student details restored';
                identityCard.querySelector('p').textContent = 'Your latest student number, name, and strand were loaded from the database.';
            }
            window.restoreSavedProfileView?.(data);

            const studentProfile = document.getElementById('studentProfile');
            if(!studentProfile) return data;

            studentProfile.innerHTML = `
            <div class="student-card">

            <div class="student-header" onclick="toggleProfile()">
                <h3>${data.studentNumber}</h3>
                <p>${data.firstName} ${data.middleInitial}. ${data.lastName}</p>
            </div>

                <div id="profileDetails" class="profile-details">

                    <p><strong>SHS strand / track:</strong> ${data.strand || 'N/A'}</p>
                    <p><strong>GWA:</strong> ${data.gwa || 'N/A'}</p>
                    <p><strong>Grades:</strong> ${data.grades || 'N/A'}</p>

                    <h4>Subjects</h4>
                    <div class="subject-table-wrap">
                        <table class="subject-table">
                            <thead><tr><th>Subject</th><th>Grade</th><th>Status</th></tr></thead>
                            <tbody>
                                ${(Array.isArray(data.subjectRows) ? data.subjectRows : []).map(row => `
                                    <tr>
                                        <td>${String(row.subject || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</td>
                                        <td>${row.grade || ''}</td>
                                        <td>${row.status || ''}</td>
                                    </tr>
                                `).join('') || '<tr><td colspan="3">No structured subjects available.</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                    <div class="profile-recommendation-action">
                        <button id="profile-generate-recommendations" type="button">Generate Course Recommendations</button>
                    </div>

                </div>

            </div>
            `;

            const profileRecommendationBtn = document.getElementById('profile-generate-recommendations');
            if(profileRecommendationBtn){
                profileRecommendationBtn.onclick = async () => {
                    profileRecommendationBtn.disabled = true;
                    profileRecommendationBtn.textContent = 'Generating...';
                    try{
                        const response = await fetch('/api/v1/generate_recommendations', { method: 'POST' });
                        const result = await response.json();
                        if(!result.success) throw new Error(result.message || 'Could not generate recommendations');
                        window.location.href = '/course';
                    }catch(error){
                        profileRecommendationBtn.disabled = false;
                        profileRecommendationBtn.textContent = 'Generate Course Recommendations';
                        alert(error.message || 'Could not generate recommendations.');
                    }
                };
            }

            return data;
        })
        .catch(error => {
            console.error("Error loading profile:", error);
        });
}

async function showSavedGrades(){
    let profile = window.latestSavedProfile;
    if(!profile){
            const response = await fetch('/api/v1/get_profile');
        profile = await response.json();
        if(profile.success) window.latestSavedProfile = profile;
    }
    const panel = document.getElementById('saved-grades-panel');
    const body = document.getElementById('saved-grades-body');
    if(!panel || !body || !profile?.success) return;
    const escapeText = value => String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    const rows = Array.isArray(profile.subjectRows) ? profile.subjectRows : [];
    body.innerHTML = rows.length
        ? rows.map(row => `<tr><td>${escapeText(row.subject)}</td><td>${escapeText(row.grade)}</td><td>${escapeText(row.status || 'Recorded')}</td></tr>`).join('')
        : '<tr><td colspan="3">No saved subjects were found.</td></tr>';
    panel.hidden = false;
    panel.scrollIntoView({behavior: 'smooth', block: 'start'});
    document.getElementById('close-saved-grades').onclick = () => { panel.hidden = true; };
}

document.addEventListener("DOMContentLoaded", () => {
    if(document.body.dataset.guest !== 'true'){
        loadSavedProfile();
    }
});

function toggleProfile() {

    const details = document.getElementById("profileDetails");

    if(details.style.display === "none" || details.style.display === "") {
        details.style.display = "block";
    }
    else {
        details.style.display = "none";
    }
}

console.log("Logout.js loaded");

function openLogout() {
    console.log("openLogout function called");
    document.getElementById("logoutPopup").style.display = "flex";
}

function closeLogout() {
    document.getElementById("logoutPopup").style.display = "none";
}
document.addEventListener('DOMContentLoaded', () => {
    const modal = document.getElementById('settings-modal');
    if (!modal) return;

    const openButtons = document.querySelectorAll('[data-open-settings]');
    const closeButton = modal.querySelector('[data-close-settings]');
    const nameForm = document.getElementById('account-name-form');
    const nameInput = document.getElementById('settings-name');
    const nameStatus = document.getElementById('settings-name-status');
    const pictureInput = document.getElementById('settings-picture-input');
    const pictureStatus = document.getElementById('settings-picture-status');
    const preview = document.getElementById('settings-preview-image');

    const setStatus = (element, message, error = false) => {
        element.textContent = message;
        element.classList.toggle('error', error);
    };

    const openSettings = () => {
        modal.hidden = false;
        document.body.classList.add('settings-is-open');
        closeButton.focus();
    };

    const closeSettings = () => {
        modal.hidden = true;
        document.body.classList.remove('settings-is-open');
    };

    openButtons.forEach(button => button.addEventListener('click', event => {
        event.preventDefault();
        openSettings();
    }));
    closeButton.addEventListener('click', closeSettings);
    modal.addEventListener('click', event => {
        if (event.target === modal) closeSettings();
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !modal.hidden) closeSettings();
    });

    modal.querySelectorAll('[data-settings-tab]').forEach(tab => {
        tab.addEventListener('click', () => {
            const selected = tab.dataset.settingsTab;
            modal.querySelectorAll('[data-settings-tab]').forEach(item => item.classList.toggle('active', item === tab));
            modal.querySelectorAll('[data-settings-panel]').forEach(panel => {
                panel.hidden = panel.dataset.settingsPanel !== selected;
                panel.classList.toggle('active', panel.dataset.settingsPanel === selected);
            });
        });
    });

    pictureInput.addEventListener('change', async () => {
        const file = pictureInput.files[0];
        if (!file) return;
        preview.src = URL.createObjectURL(file);
        setStatus(pictureStatus, 'Uploading...');
        const formData = new FormData();
        formData.append('profile_picture', file);
        try {
            const response = await fetch('/api/v1/upload_profile_picture', { method: 'POST', body: formData });
            const data = await response.json();
            if (!data.success) throw new Error(data.message || 'Upload failed.');
            const imageUrl = `${data.profile_image}?t=${Date.now()}`;
            preview.src = imageUrl;
            document.querySelectorAll('#profilePicture, .focus-profile-trigger img').forEach(image => { image.src = imageUrl; });
            setStatus(pictureStatus, 'Picture updated.');
        } catch (error) {
            setStatus(pictureStatus, error.message, true);
        }
    });

    nameForm.addEventListener('submit', async event => {
        event.preventDefault();
        setStatus(nameStatus, 'Saving...');
        try {
            const response = await fetch('/api/v1/update_account', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: nameInput.value })
            });
            const data = await response.json();
            if (!data.success) throw new Error(data.message || 'Could not save your name.');
            nameInput.value = data.name;
            document.querySelectorAll('.focus-account > span, .profile-menu-header strong').forEach(element => { element.textContent = data.name; });
            document.querySelectorAll('.tooltip').forEach(element => { element.innerHTML = `StudentPathFinder Account<br>${data.name}<br>${element.innerHTML.split('<br>').pop()}`; });
            setStatus(nameStatus, 'Name updated.');
        } catch (error) {
            setStatus(nameStatus, error.message, true);
        }
    });
});

document.addEventListener('DOMContentLoaded', () => {
    const toggle = document.querySelector('.mobile-menu-toggle');
    const sidebar = document.querySelector('.sidebar');
    const profile = document.querySelector('.usercontainer');
    if (!toggle || !sidebar) return;
    if (profile && window.matchMedia('(max-width: 900px)').matches) {
        sidebar.appendChild(profile);
        profile.classList.add('mobile-profile-menu-item');
    }
    toggle.addEventListener('click', () => {
        const open = sidebar.classList.toggle('mobile-menu-open');
        toggle.setAttribute('aria-expanded', String(open));
        toggle.innerHTML = open ? '<i class="fa-solid fa-xmark"></i>' : '<i class="fa-solid fa-bars"></i>';
    });
});