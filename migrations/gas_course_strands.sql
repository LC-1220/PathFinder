-- Add GAS eligibility only to existing courses; preserve all other strand tags.
insert into public.university_course_strands (course, strand_code)
select course, 'GAS'
from public.university_courses
where course in (
    'BS in Accountancy',
    'BS in Business Administration',
    'BS in Business Administration major in Human Resource Management',
    'BS in Business Administration major in Marketing Management',
    'BS in Entrepreneurship',
    'Bachelor of Arts in Communication',
    'Bachelor of Arts in Psychology',
    'Bachelor of Science in Psychology',
    'BS in Criminology',
    'BS in Tourism Management',
    'BS in Hospitality Management',
    'Bachelor of Early Childhood Education',
    'Bachelor of Elementary Education',
    'Bachelor of Physical Education',
    'Bachelor of Secondary Education',
    'Bachelor of Special Needs Education'
)
on conflict (course, strand_code) do nothing;
