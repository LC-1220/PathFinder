function displayCourseName(value){
  return String(value ?? '').replace(/^BS\b\.?/i, 'Bachelor of Science');
}
