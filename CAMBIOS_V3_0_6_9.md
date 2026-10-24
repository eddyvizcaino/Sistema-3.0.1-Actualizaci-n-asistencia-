# V3.0.6.9 — corrección alta de materias (base V3.0.6.6)

- Sincroniza en PostgreSQL la secuencia SERIAL de FSubjects con MAX(Id) después de sembrar las 15 materias oficiales. Corrige colisión probable de clave primaria al crear materias nuevas.
- Agregar materia solo pide nombre; no modifica los maestros existentes.
- Captura errores de guardado, hace rollback y registra excepción en logs.
- Rechaza nombres duplicados ignorando mayúsculas y espacios exteriores.
- No borra materias ni inscripciones.

**Límite:** no se han incluido todavía cambios de familia, cónyuges ni modalidad presencial/virtual. Estos deben implementarse y probarse por separado. No se probó en PostgreSQL real.
