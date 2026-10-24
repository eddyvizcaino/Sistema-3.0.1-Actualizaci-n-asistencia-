# V3.1.0 - Reportes de asistencia
- Panel mensual por culto y ministerio, con gráficos y visitantes.
- Alertas pastorales basadas en dos viernes con registros; consulta restringida a roles pastorales.
- Exportación PDF, Excel e impresión con filtros.
- Sin migraciones destructivas.

## Limitaciones
- Los cultos sin registros no se conocen, por lo que no se consideran en alertas.
- Las alertas son globales, no limitadas al mes seleccionado.
- El ministerio mostrado es el actual, no una instantánea histórica.
- Visitantes son registros, no personas únicas deduplicadas.
- No se ha probado con PostgreSQL real en Render.
