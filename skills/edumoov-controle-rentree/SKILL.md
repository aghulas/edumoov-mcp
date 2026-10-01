---
name: "edumoov-controle-rentree"
description: "Contrôler la cohérence Edumoov ↔ Charlemagne à la rentrée ou après un changement : classes, enseignants affectés, effectifs élèves, licences Livret/Cartable/Journal, réglages Livret."
---

# Contrôle de cohérence Edumoov ↔ Charlemagne

À utiliser en septembre-octobre, après un changement d'enseignant ou une arrivée/un départ d'élèves, ou quand Rémi demande si Educartable « est à jour ». Lecture seule par défaut.

## Principes

- Charlemagne (ERP de l'école) fait référence pour les élèves, les classes et le personnel ; Edumoov doit le refléter.
- Une seule source n'est jamais suffisante : c'est la comparaison croisée qui a révélé en 2026 les 5 classes invisibles (pagination). Si un écart est massif ou régulier, suspecter d'abord un bug du connecteur (skill maintenance-connecteur-edumoov) avant d'accuser les données.
- Ne jamais afficher INE, dates de naissance ni coordonnées ; comparer sur nom + prénom normalisés (minuscules, sans accents, tirets/espaces unifiés).
- Limiter les appels : une liste d'élèves par classe, pas de boucle inutile.

## Étapes

1. **Classes** : `edumoov_classrooms_list` (15 classes attendues à la rentrée 2026 ; ignorer `release`/`deleted`) vs classes des élèves actifs de `charlemagne__liste_eleves`. Apparier par niveau + nom d'enseignant ; signaler classe sans correspondance, nom divergent, `inc` (n° ONDE) manquant.
2. **Enseignants** : `edumoov_school_teachers_list` + enseignants de chaque classe (`edumoov_classroom_get`) vs `charlemagne__liste_personnels`. Signaler : classe sans titulaire, enseignant parti encore affecté, nouvel enseignant sans compte ou sans classe, rôles (TIT, DECL, REMP, ATSEM, AESH).
3. **Élèves** : par classe, `edumoov_classroom_pupils_list` vs `charlemagne__liste_eleves(classe=…)`. Lister : effectifs des deux côtés, élèves présents d'un seul côté, élève dans une autre classe. Les élèves sortis (`actifs_seulement=True` côté Charlemagne) ne doivent plus apparaître dans Edumoov.
4. **Licences** : `edumoov_subscriptions_list(active_only=True)`. Vérifier que chaque classe a Livret (NOTEBOOK) et Cartable (CARTABLE) au-delà de la prochaine échéance (14/10) et que chaque enseignant qui utilise le Journal (CLOG) est couvert ; signaler factures impayées. Voir l'état des licences dans `claude/spec-ecriture-edumoov.md` §6.2 (projet Claude).
5. **Réglages Livret** (si demandé) : `edumoov_classroom_settings_get` par classe ; signaler les classes dont barème/périodes diffèrent de la majorité.

## Restitution

- Dans la réponse : synthèse courte (ce qui est conforme, nombre d'écarts par rubrique) puis tableau des écarts à traiter, avec l'action proposée et qui doit la faire (secrétariat, direction, enseignant, Rémi).
- Si Rémi veut garder ou partager le contrôle : en faire un document (Docs) daté plutôt qu'un fichier.

## Corriger

- Fiche classe (nom, `inc`, enseignant affiché) : `edumoov_classroom_prepare_update`, aperçu puis confirmation explicite.
- Affectation d'enseignant : `edumoov_classroom_prepare_teacher` — méthode non validée en réel au 01/10/2026 : proposer d'abord un cas réversible et vérifier dans Edumoov, sinon faire la correction dans l'interface Direction d'Edumoov.
- Élèves : aucun outil d'écriture élèves dans le connecteur (volontaire) — corrections dans l'interface Edumoov ; si l'erreur vient de Charlemagne, la traiter dans Charlemagne (skills Charlemagne).
- Licences : commande/renouvellement dans Direction → Licences et factures d'Edumoov, après accord de la direction.