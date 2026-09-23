from pathlib import Path
path = Path('static/SM.js')
text = path.read_text(encoding='utf-8')
start = text.index('function parseStudent(text){')
end = text.index('function autoAddStudentFromText(text){')
replacement = '''function parseStudent(text){
    if(!text){
        console.warn('parseStudent: no text provided');
        return null;
    }

    const normalizeText = raw => raw
        .replace(/\r/g, '\n')
        .replace(/\u00A0/g, ' ')
        .replace(/\t+/g, ' ')
        .replace(/-\n/g, '')
        .replace(/\n{2,}/g, '\n')
        .replace(/ {2,}/g, ' ')
        .trim();

    const normalized = normalizeText(text);
    const lines = normalized.split(/\n+/).map(l => l.trim()).filter(Boolean);

    const findValue = patterns => {
        for(const line of lines){
            for(const pattern of patterns){
                const match = line.match(pattern);
                if(match && match[1]) return match[1].trim();
            }
        }
        return '';
    };

    const findLine = pattern => lines.find(l => pattern.test(l)) || '';

    const parseStudentNumber = raw => {
        const digits = (raw || '').replace(/[^0-9]/g, '');
        if(digits.length === 9) return `${digits.slice(0,2)}-${digits.slice(2,6)}-${digits.slice(6)}`;
        return (raw || '').trim();
    };

    const parseName = raw => {
        const value = (raw || '').replace(/\s{2,}/g, ' ').trim();
        if(!value) return { firstName: '', middleInitial: '', lastName: '' };
        if(value.includes(',')){
            const [last, rest] = value.split(',').map(p => p.trim());
            const parts = rest.split(/\s+/).filter(Boolean);
            return {
                firstName: parts.slice(0,2).join(' '),
                middleInitial: parts[2] ? parts[2].charAt(0) : (parts[1] ? parts[1].charAt(0) : ''),
                lastName: last
            };
        }
        const parts = value.split(/\s+/).filter(Boolean);
        if(parts.length === 1) return { firstName: parts[0], middleInitial: '', lastName: '' };
        if(parts.length === 2) return { firstName: parts[0], middleInitial: '', lastName: parts[1] };
        return {
            firstName: parts[0],
            middleInitial: parts.slice(1, -1).map(p => p[0]).join(''),
            lastName: parts[parts.length - 1]
        };
    };

    const parseSubjectRows = () => {
        const stopPattern = /\b(gpa|gwa|average|overall|remarks|school|date|term|semester|principal|registrar|certified|prepared by)\b/i;
        const rows = lines.slice();
        const subjects = [];

        for(let i = 0; i < rows.length; i++){
            const line = rows[i];
            if(stopPattern.test(line)) break;

            const codeMatch = line.match(/\b[A-Z]{2,4}\s*\d{3,4}\b/);
            const gradeMatch = line.match(/\b(?:[A-F][+-]?|Passed|Failed|Incomplete|[0-9]{1,3}(?:\.[0-9]+)?)\b/i);
            if(!codeMatch && !gradeMatch) continue;

            let title = line;
            if(codeMatch) title = title.replace(codeMatch[0], '');
            if(gradeMatch) title = title.replace(gradeMatch[0], '');
            title = title.replace(/[:\-\|]+/g, ' ').trim();
            if(!title && rows[i+1]){
                const next = rows[i+1].replace(/[:\-\|]+/g, ' ').trim();
                if(next && !/\b(?:[A-F][+-]?|Passed|Failed|Incomplete|[0-9]{1,3}(?:\.[0-9]+)?)\b/i.test(next)){
                    title = next;
                }
            }
            if(!title && codeMatch) title = codeMatch[0];
            if(codeMatch && title && !title.startsWith(codeMatch[0])) title = `${codeMatch[0]} - ${title}`;
            const grade = gradeMatch ? gradeMatch[0].trim() : '';
            if(title) subjects.push(`${title}${grade ? ' - ' + grade : ''}`);
        }
        return [...new Set(subjects)].join('\n');
    };

    const studentNumberRaw = findValue([
        /(?:student\s*(?:number|no|id|#)|std\.?\s*no\.?|student\s*id)\s*[:\-]?\s*([A-Za-z0-9\-\s]+)/i,
        /(\b\d{9}\b)/
    ]);
    const studentNumber = parseStudentNumber(studentNumberRaw);

    const rawName = findValue([
        /(?:student\s*name|name\s*of\s*student|name)\s*[:\-]?\s*(.+)/i
    ]) || findLine(/^[A-Za-z][A-Za-z\s\-\.]{3,}$/);
    const nameParts = parseName(rawName || 'Unknown');

    const courseText = findValue([
        /(?:course\s*\/\s*major|course|program|strand|track)\s*[:\-]?\s*([A-Za-z0-9 \/&\-]+)/i
    ]) || findLine(/\b(course|program|strand|track)\b/i).replace(/(?:course\s*[:\-]?|program\s*[:\-]?|strand\s*[:\-]?|track\s*[:\-]?)/i, '').trim();

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

    const subjects = parseSubjectRows();
    const finalStudentNumber = studentNumber || ('S' + (Date.now() % 1000000).toString().padStart(6, '0'));

    return {
        firstName: nameParts.firstName || 'Unknown',
        middleInitial: nameParts.middleInitial || '',
        lastName: nameParts.lastName || '',
        studentNumber: finalStudentNumber,
        course: courseText || '',
        academicRecord: age || '',
        gwa: gwa || '',
        grades: grades || '',
        subjects: subjects || ''
    };
}
'''
new_text = text[:start] + replacement + text[end:]
path.write_text(new_text, encoding='utf-8')
print('replacement done')
