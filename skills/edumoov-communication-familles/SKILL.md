---
name: "edumoov-communication-familles"
description: "Rédiger et diffuser un message aux familles dans Educartable (Edumoov) : annonce d'école, cahier de liaison d'une classe, réponse à une famille — brouillon, aperçu, confirmation."
---

# Communication aux familles via Educartable (Edumoov)

À utiliser quand Rémi veut informer des familles de l'École Sainte-Marie, poster dans le cahier de liaison d'une classe, ou répondre à une famille dans un fil Educartable.

## 1. Choisir le bon canal

- **Educartable** = vie de classe et échanges parents–enseignants.
- **EcoleDirecte** = administratif / secrétariat (documents, démarches, factures) : orienter vers EcoleDirecte (skills EcoleDirecte) plutôt qu'Edumoov dans ce cas. Une annonce Educartable peut toutefois servir à renvoyer toutes les familles vers EcoleDirecte (ex. annonce du 01/10/2026 sur les factures).
- **Espace Documents d'EcoleDirecte ≠ message** : un document publié depuis Charlemagne dans l'espace Documents n'envoie **aucune notification** aux familles (cas de l'invitation CM2 du 01/10/2026). Une information à faire lire passe par un message (EcoleDirecte `ed_perso_message_ecrire` en brouillon avec `pieces_jointes`, et/ou Educartable), le document en pièce jointe. Un document déjà publié se récupère avec `ed_admin_documents_famille` puis `ed_admin_document_telecharger` (enregistré sous `~/Charlemagne/ecoledirecte_documents`).
- Dans Educartable :
  - **Annonce d'école** (`edumoov_advert_prepare_*`) : message de la direction à **toute l'école**. Corps converti en HTML.
  - **Cahier de liaison** (`edumoov_cahier_prepare_*`) : message à une classe ou à certains élèves d'une classe ; `info` (message) ou `alert` (mot important) ; accusé de lecture possible (`acknowledgement="read"`) ou réponse demandée (`"comment"`).
  - **Commentaire** (`edumoov_comment_prepare_create`) : répondre dans le fil d'un message existant (lire d'abord `edumoov_cartable_item_comments_list`).
- **Cibler une ou quelques classes = un message du cahier de liaison par classe, pas une annonce.** L'interface Direction n'affiche pas les classes destinataires d'une annonce : une annonce limitée aux CM2 semblait partir à toute l'école et Rémi l'a supprimée (06/10/2026).
- Les messages partent au nom du compte direction (« Ecole Sainte Marie FONTAINEBLEAU ») : signer « La direction – École Sainte Marie ». Un message de classe court-circuite l'enseignant : vérifier avec Rémi que c'est voulu, ou proposer de prévenir l'enseignant.

## 2. Rédiger

- Vouvoiement, ton cordial et sobre, phrases courtes ; titre explicite (objet, date si événement).
- Dates absolues plutôt que relatives (« le 30 septembre » plutôt que « hier ») : une annonce reste lue plusieurs jours.
- Texte brut pour le cahier de liaison (paragraphes séparés par une ligne vide). Si le brouillon risque d'être retouché dans l'interface, éviter les listes à sauts de ligne simples : l'éditeur convertit le texte en HTML et peut fusionner ces lignes (constaté le 06/10/2026) — une ligne vide entre chaque élément. Pas de données personnelles d'autres familles ou d'élèves dans un message collectif.
- Indiquer qui contacter et comment (enseignant via Educartable, secrétariat via EcoleDirecte ou support@saintemarie-fontainebleau.fr pour les questions EcoleDirecte).
- Si le même message part aussi par EcoleDirecte, reprendre le même texte en n'adaptant que la signature.
- Montrer d'abord le texte à Rémi dans la conversation et l'ajuster avant toute préparation.

## 3. Destinataires

- Classes : `edumoov_classrooms_list` (identifiant + nom). Élèves : `edumoov_classroom_pupils_list` ; ne jamais deviner un identifiant.
- Annonce : `classroom_ids=None` = toutes les classes actives (15 à la rentrée 2026). Cahier : `pupil_ids=None` = toute la classe.
- Répéter dans l'aperçu le nombre de classes / d'élèves concernés.

## 4. Pièces jointes

- Annonce : `edumoov_advert_prepare_attach(advert_id, fichier)` — pdf/png/jpg, 2 Mo au plus.
- Cahier de liaison : `edumoov_cahier_prepare_attach(classroom_id, message_id, fichier)` — pdf/png/jpg, 10 Mo au plus, une fois par message (une classe = un message).
- Fichier local sous `~/Charlemagne` (`EDUMOOV_ATTACH_ROOTS`) ; documents bancaires refusés. Edumoov retire du nom tous les caractères non alphanumériques : donner au fichier un nom lisible sans espaces (ex. `InvitationEntree6eCollegeJeanneDArc.pdf`).
- Joindre sur le **brouillon**, avant publication (sur un message déjà publié, la pièce jointe apparaît aussitôt aux familles). Même deux temps : aperçu + jeton, puis `edumoov_write_confirm`.

## 5. Diffuser en deux temps

1. Préparer en **brouillon** (`publication="draft"` pour une annonce ; le cahier est toujours créé en brouillon), puis joindre les fichiers. Si Rémi a validé le texte et demandé la diffusion, la création du brouillon et l'ajout de la pièce jointe peuvent être confirmés sans attendre (aucune famille notifiée).
2. Montrer l'aperçu complet (titre, texte, destinataires, pièces jointes, avertissements) ; n'appeler `edumoov_write_confirm` pour la publication qu'après un « oui » explicite de Rémi.
3. Publier par une seconde préparation (`edumoov_advert_prepare_visibility` publish/schedule, ou `edumoov_cahier_prepare_visibility` publish), avec le nombre de familles notifiées affiché, puis nouvelle confirmation explicite. Le jeton expire au bout de 10 minutes : en cas de délai, re-préparer.
4. Vérifier ensuite (`edumoov_adverts_list` ou `edumoov_cartable_items_list`), donner le statut à Rémi et l'inviter à contrôler l'affichage dans l'interface Educartable.

- Une publication notifie immédiatement les familles : aucune publication de sa propre initiative, jamais de jeton confirmé par anticipation.
- État de validation en réel : **publication immédiate d'une annonce d'école validée le 01/10/2026** (15 classes, affichage HTML et notification conformes) ; pièces jointes validées sur brouillon le 06/10/2026. **Cahier de liaison** : brouillons préparés par le connecteur (avec PDF joint) puis relus et publiés par la direction dans l'interface le 06/10/2026 (CM2 A et B, 30 familles chacune, accusés de lecture reçus) — chemin validé ; la publication par `edumoov_cahier_prepare_visibility` n'a pas encore servi en réel. Pas encore validés : publication programmée, dépublication — pour une première, vérifier dans l'interface et signaler l'issue pour mettre à jour la spec (skill maintenance-connecteur-edumoov).
- Variante courante : préparer les brouillons et laisser la direction les relire et les publier elle-même dans Educartable ; prévenir Rémi avec un brouillon de mail à direction@ (vouvoiement).
- Si les outils d'écriture refusent (`EDUMOOV_ENABLE_WRITES` absent, ou serveur Azure) : fournir le texte prêt à coller dans l'interface Educartable.

## 6. Familles sans compte Educartable : codes familles

- Un message du cahier de liaison n'atteint pas un élève dont aucun parent n'est inscrit (liste `parents` vide dans `edumoov_classroom_pupils_list`). Pour ces familles : fiche des codes familles + mail.
- `edumoov_family_codes_pdf(classroom_id, pupil_ids)` produit une fiche PDF par élève (même modèle que « Codes familles › Exporter en pdf » d'Educartable) dans `~/Charlemagne/edumoov/codes/`. Les codes n'apparaissent jamais dans la conversation : ne pas ouvrir ni recopier les fiches.
- Mail aux parents de l'élève (adresses : `responsables_eleves` du connecteur Charlemagne), depuis support@saintemarie-fontainebleau.fr, secrétariat en copie cachée : Educartable est l'outil de communication entre les parents et l'enseignant(e), il faut activer le compte pour être informé et contacté ; rappel EcoleDirecte si besoin. Brouillon Outlook (Rémi choisit l'expéditeur support@ et envoie) ; la fiche de **cet** élève seulement est jointe à la main (le connecteur Microsoft 365 ne joint pas de fichier).
- Avant d'écrire à une famille, vérifier dans Charlemagne que l'élève est toujours inscrit (`actif`) : un élève sorti peut rester visible dans EcoleDirecte (cas du 07/10/2026).

## 7. Corriger ou retirer

- Modifier : `edumoov_advert_prepare_update` / `edumoov_cahier_prepare_update` (un message déjà publié est modifié sous les yeux des familles — le signaler).
- Retirer : `*_prepare_visibility` unpublish (les notifications déjà parties restent) ou `*_prepare_delete`.
- Commentaire inapproprié : `edumoov_comment_prepare_delete` (suppression douce).
