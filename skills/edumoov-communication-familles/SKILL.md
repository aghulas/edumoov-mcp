---
name: "edumoov-communication-familles"
description: "Rédiger et diffuser un message aux familles dans Educartable (Edumoov) : annonce d'école, cahier de liaison d'une classe, réponse à une famille — brouillon, aperçu, confirmation."
---

# Communication aux familles via Educartable (Edumoov)

À utiliser quand Rémi veut informer des familles de l'École Sainte-Marie, poster dans le cahier de liaison d'une classe, ou répondre à une famille dans un fil Educartable.

## 1. Choisir le bon canal

- **Educartable** = vie de classe et échanges parents–enseignants.
- **EcoleDirecte** = administratif / secrétariat (documents, démarches, factures) : orienter vers EcoleDirecte (skills EcoleDirecte) plutôt qu'Edumoov dans ce cas.
- Dans Educartable :
  - **Annonce d'école** (`edumoov_advert_prepare_*`) : message de la direction à plusieurs classes ou à toute l'école. Corps converti en HTML.
  - **Cahier de liaison** (`edumoov_cahier_prepare_*`) : message à une classe ou à certains élèves d'une classe ; `info` (message) ou `alert` (mot important) ; accusé de lecture possible (`acknowledgement="read"`) ou réponse demandée (`"comment"`).
  - **Commentaire** (`edumoov_comment_prepare_create`) : répondre dans le fil d'un message existant (lire d'abord `edumoov_cartable_item_comments_list`).
- Les messages partent au nom du compte direction (« Ecole Sainte Marie FONTAINEBLEAU »). Un message de classe court-circuite l'enseignant : vérifier avec Rémi que c'est voulu, ou proposer de prévenir l'enseignant.

## 2. Rédiger

- Vouvoiement, ton cordial et sobre, phrases courtes ; titre explicite (objet, date si événement).
- Texte brut pour le cahier de liaison (paragraphes séparés par une ligne vide). Pas de données personnelles d'autres familles ou d'élèves dans un message collectif.
- Indiquer qui contacter et comment (enseignant via Educartable, secrétariat via EcoleDirecte ou support@saintemarie-fontainebleau.fr pour les questions EcoleDirecte).
- Montrer d'abord le texte à Rémi dans la conversation et l'ajuster avant toute préparation.

## 3. Destinataires

- Classes : `edumoov_classrooms_list` (identifiant + nom). Élèves : `edumoov_classroom_pupils_list` ; ne jamais deviner un identifiant.
- Annonce : `classroom_ids=None` = toutes les classes actives ; sinon liste explicite. Cahier : `pupil_ids=None` = toute la classe.
- Répéter dans l'aperçu le nombre de classes / d'élèves concernés.

## 4. Diffuser en deux temps

1. Préparer en **brouillon** (`publication="draft"` pour une annonce ; le cahier est toujours créé en brouillon).
2. Montrer l'aperçu complet (titre, texte, destinataires, avertissements) ; n'appeler `edumoov_write_confirm` qu'après un « oui » explicite de Rémi.
3. Publier par une seconde préparation (`edumoov_advert_prepare_visibility` publish/schedule, ou `edumoov_cahier_prepare_visibility` publish), avec le nombre de familles notifiées affiché, puis nouvelle confirmation explicite.
4. Vérifier ensuite (`edumoov_adverts_list` ou `edumoov_cartable_items_list`) et donner le statut à Rémi.

- Une publication notifie immédiatement les familles : aucune publication de sa propre initiative, jamais de jeton confirmé par anticipation.
- La publication réelle n'a pas encore été validée en conditions réelles (au 01/10/2026) : pour la première fois, proposer une diffusion restreinte (une classe ou un élève) et vérifier le résultat dans l'interface Educartable ; signaler l'issue pour mettre à jour la spec (skill maintenance-connecteur-edumoov).
- Si les outils d'écriture refusent (`EDUMOOV_ENABLE_WRITES` absent, ou serveur Azure) : fournir le texte prêt à coller dans l'interface Educartable.

## 5. Corriger ou retirer

- Modifier : `edumoov_advert_prepare_update` / `edumoov_cahier_prepare_update` (un message déjà publié est modifié sous les yeux des familles — le signaler).
- Retirer : `*_prepare_visibility` unpublish (les notifications déjà parties restent) ou `*_prepare_delete`.
- Commentaire inapproprié : `edumoov_comment_prepare_delete` (suppression douce).